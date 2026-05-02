#!/usr/bin/env python3
"""
Component 7: Watcher — tails JSONL files and ingests new rounds incrementally.

Polls the Claude Code projects dir every 30s for new lines in any .jsonl file.
Tracks last-seen line count per file in a small state file (watcher-state.json).

Usage:
  python tools/changelog/watcher.py [--interval 30] [--skip-oracle]

Or as a launchd service (see watcher.plist).
Logs to: tools/changelog/watcher.log
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-Users-blaze-Movies-Kin"
STATE_FILE = HERE / "watcher-state.json"
LOG_FILE = HERE / "watcher.log"

PARSE_SCRIPT = HERE / "parse-jsonl.py"
FILTER_SCRIPT = HERE / "privacy-filter.py"
INGEST_SCRIPT = HERE / "ingest-to-oracle.py"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watcher] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def load_state() -> dict[str, int]:
    """Load per-file last-seen line counts."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict[str, int]):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def count_lines(path: Path) -> int:
    """Count lines in file without loading full content."""
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def tail_lines(path: Path, from_line: int) -> list[str]:
    """Return lines from from_line (0-indexed) to end of file."""
    lines = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= from_line:
                    lines.append(line)
    except Exception as e:
        log.warning(f"tail_lines error on {path}: {e}")
    return lines


def process_new_lines(path: Path, new_lines: list[str], skip_oracle: bool) -> int:
    """Run new_lines through the parse/filter/ingest pipeline. Returns count ingested."""
    if not new_lines:
        return 0

    # Write new lines to a temp JSONL for processing
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as tmp:
        tmp_path = tmp.name
        tmp.writelines(new_lines)

    try:
        skip_flag = ["--skip-oracle"] if skip_oracle else []
        cmd = (
            f"python3 {PARSE_SCRIPT} {tmp_path} "
            f"| python3 {FILTER_SCRIPT} "
            f"| python3 {INGEST_SCRIPT} {' '.join(skip_flag)}"
        )
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        )
        # Count from stderr output
        lines = result.stderr.strip().split("\n")
        for line in lines:
            if "ingest-to-oracle" in line:
                log.info(f"  {line.strip()}")
        return len(new_lines)
    except subprocess.TimeoutExpired:
        log.warning("Pipeline timed out for %s", path.name)
        return 0
    except Exception as e:
        log.warning(f"Pipeline error: {e}")
        return 0
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def check_once(state: dict[str, int], skip_oracle: bool) -> dict[str, int]:
    """Check all JSONL files for new lines. Returns updated state."""
    if not PROJECTS_DIR.exists():
        log.warning(f"Projects dir not found: {PROJECTS_DIR}")
        return state

    jsonl_files = list(PROJECTS_DIR.glob("*.jsonl"))
    if not jsonl_files:
        return state

    for fpath in jsonl_files:
        key = fpath.name
        last_seen = state.get(key, 0)
        current_count = count_lines(fpath)

        if current_count <= last_seen:
            continue

        new_lines = tail_lines(fpath, last_seen)
        if new_lines:
            log.info(f"{key}: {len(new_lines)} new lines (total {current_count})")
            process_new_lines(fpath, new_lines, skip_oracle)
            state[key] = current_count

    return state


def main():
    ap = argparse.ArgumentParser(description="Watch JSONL files for new rounds and ingest them")
    ap.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default 30)")
    ap.add_argument("--skip-oracle", action="store_true", help="Skip Oracle ingest (local DB only)")
    ap.add_argument("--once", action="store_true", help="Run one check then exit")
    args = ap.parse_args()

    log.info(f"Watcher starting (interval={args.interval}s, skip_oracle={args.skip_oracle})")
    log.info(f"Watching: {PROJECTS_DIR}")

    state = load_state()

    if args.once:
        state = check_once(state, args.skip_oracle)
        save_state(state)
        log.info("One-shot check complete.")
        return

    while True:
        try:
            state = check_once(state, args.skip_oracle)
            save_state(state)
        except KeyboardInterrupt:
            log.info("Watcher stopped.")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
