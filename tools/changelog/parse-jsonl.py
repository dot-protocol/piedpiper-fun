#!/usr/bin/env python3
"""
Component 1: JSONL → events parser

Reads a Claude Code session JSONL file and extracts (user, assistant) round pairs.
Each round gets a stable unique ID: round-<session-prefix>-<timestamp-ms>

Usage:
  python parse-jsonl.py <session.jsonl> [--out events.ndjson]

Output: newline-delimited JSON, one line per message:
  {id, session_id, timestamp, role, content, tool_uses_count, msg_index}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import timezone
from typing import Iterator


def _extract_text(content) -> str:
    """Pull plain text from Claude content (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type", "")
                if t == "text":
                    parts.append(block.get("text", ""))
                elif t == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str):
                        parts.append(f"[tool_result: {inner[:200]}]")
                    elif isinstance(inner, list):
                        for ib in inner:
                            if isinstance(ib, dict) and ib.get("type") == "text":
                                parts.append(f"[tool_result: {ib.get('text','')[:200]}]")
                elif t == "thinking":
                    pass  # skip internal thinking blocks
        return "\n".join(parts)
    return str(content)


def _count_tool_uses(content) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")


def _session_prefix(session_id: str) -> str:
    """Rocky-mac-<first-8-chars> — human-readable stable prefix."""
    return f"rocky-mac-{session_id[:8]}"


def parse_session(path: str) -> Iterator[dict]:
    """Yield one dict per extractable message (user or assistant) from a JSONL file."""
    session_id = os.path.splitext(os.path.basename(path))[0]
    prefix = _session_prefix(session_id)

    with open(path, encoding="utf-8") as fh:
        for idx, raw in enumerate(fh):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Claude Code JSONL wraps messages inside "message" key
            msg = obj.get("message", {})
            role = msg.get("role") or obj.get("role", "")
            if role not in ("user", "assistant"):
                continue

            content_raw = msg.get("content", obj.get("content", ""))
            text = _extract_text(content_raw)
            if not text.strip():
                continue

            tool_uses = _count_tool_uses(content_raw)

            # Timestamp: try message timestamp, fall back to msg_index
            ts_str = msg.get("timestamp") or obj.get("timestamp") or ""
            if ts_str:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts_ms = int(dt.timestamp() * 1000)
                except Exception:
                    ts_ms = idx
            else:
                ts_ms = idx  # will be replaced by msg_index-based ordering

            round_id = f"round-{prefix}-{ts_ms}-{idx}"

            yield {
                "id": round_id,
                "session_id": session_id,
                "timestamp_ms": ts_ms,
                "timestamp_iso": ts_str or None,
                "role": role,
                "content": text,
                "tool_uses_count": tool_uses,
                "msg_index": idx,
            }


def main():
    ap = argparse.ArgumentParser(description="Parse Claude Code JSONL → events NDJSON")
    ap.add_argument("jsonl", help="Path to session .jsonl file")
    ap.add_argument("--out", default="-", help="Output file (default: stdout)")
    args = ap.parse_args()

    out = open(args.out, "w", encoding="utf-8") if args.out != "-" else sys.stdout
    count = 0
    try:
        for event in parse_session(args.jsonl):
            out.write(json.dumps(event, ensure_ascii=False) + "\n")
            count += 1
    finally:
        if args.out != "-":
            out.close()

    print(f"[parse-jsonl] {count} messages extracted from {args.jsonl}", file=sys.stderr)


if __name__ == "__main__":
    main()
