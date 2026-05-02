#!/usr/bin/env python3
"""
Component 3: Oracle ingestion + local events store

Reads filtered events (NDJSON) and:
1. Writes to local SQLite events store at tools/changelog/events.db
2. POSTs to Oracle VPS via SSH oracle CLI (idempotent on round_id)

Usage:
  python parse-jsonl.py <session.jsonl> | python privacy-filter.py | python ingest-to-oracle.py

Environment:
  ORACLE_TOKEN   — VPS Bearer token (default: read from .env or state)
  ORACLE_SSH     — SSH alias for VPS (default: adrian)
  SKIP_ORACLE    — set to "1" to skip Oracle ingest (local DB only)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent
DB_PATH = HERE / "events.db"

ORACLE_TOKEN = os.environ.get(
    "ORACLE_TOKEN",
    "sHSqxUNa4PHn4FtEFXgypZmforoRwFYSZgEmFimzJtLQzY5HIH85JL7g-8Wa3H4j",
)
ORACLE_SSH = os.environ.get("ORACLE_SSH", "adrian")
ORACLE_URL = "https://oracle.axxis.world"
ORACLE_CHANNEL = "pied-piper-live"
SKIP_ORACLE = os.environ.get("SKIP_ORACLE", "0") == "1"


# ---------------------------------------------------------------------------
# Local SQLite events store
# ---------------------------------------------------------------------------
def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id           TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL,
            timestamp_ms INTEGER,
            timestamp_iso TEXT,
            role         TEXT NOT NULL,
            content      TEXT NOT NULL,
            tool_uses    INTEGER DEFAULT 0,
            msg_index    INTEGER DEFAULT 0,
            channel      TEXT NOT NULL DEFAULT 'general',
            ingested_at  TEXT DEFAULT (datetime('now')),
            oracle_sent  INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ch ON events(channel)")
    conn.commit()
    return conn


def upsert_event(conn: sqlite3.Connection, event: dict) -> bool:
    """Insert event into local DB. Returns True if new, False if already exists."""
    existing = conn.execute(
        "SELECT id FROM events WHERE id = ?", (event["id"],)
    ).fetchone()
    if existing:
        return False

    conn.execute("""
        INSERT INTO events
            (id, session_id, timestamp_ms, timestamp_iso, role, content,
             tool_uses, msg_index, channel)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event["id"],
        event.get("session_id", ""),
        event.get("timestamp_ms", 0),
        event.get("timestamp_iso"),
        event.get("role", ""),
        event.get("content", ""),
        event.get("tool_uses_count", 0),
        event.get("msg_index", 0),
        event.get("channel", "general"),
    ))
    conn.commit()
    return True


def mark_oracle_sent(conn: sqlite3.Connection, event_id: str):
    conn.execute("UPDATE events SET oracle_sent=1 WHERE id=?", (event_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Oracle ingest via SSH
# ---------------------------------------------------------------------------
def ingest_to_oracle_ssh(event: dict) -> bool:
    """
    Call Oracle CLI on VPS via SSH to ingest one event.
    Uses: ssh adrian "cd /opt/tree && python cli.py ingest ..."
    Returns True on success.
    """
    content = event.get("content", "")[:2000]  # cap at 2KB per observation
    role = event.get("role", "unknown")
    channel = event.get("channel", "general")
    round_id = event["id"]
    ts = event.get("timestamp_iso") or ""

    # Build observation payload
    item = {
        "content": f"[{role.upper()}] {content}",
        "type": "context",
        "rationale": f"Engineering-in-public session log. Round: {round_id}",
        "tags": [ORACLE_CHANNEL, channel, role, "changelog", "public"],
    }
    payload = json.dumps({
        "source": f"pied-piper-live-{round_id}",
        "extracted": {
            "items": [item],
        },
    })

    # We use curl via SSH since the MCP RPC endpoint requires MCP protocol
    # The VPS Oracle REST only has /health, /metrics, /stream, /resonate
    # So we call the oracle CLI directly
    cmd = [
        "ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
        ORACLE_SSH,
        f"cd /opt/tree && python3 cli.py ingest '{payload}' 2>/dev/null || "
        f"python3 -c \""
        f"import sys; sys.path.insert(0, '/opt/tree'); "
        f"from tree_store import TreeStore; from tree_ingest import TreeIngest; "
        f"import asyncio, json; "
        f"payload = json.loads(sys.argv[1]); "
        f"\" '{payload}' 2>/dev/null",
    ]

    # Simpler: use curl with the MCP streamable HTTP endpoint
    mcp_payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {
            "name": "oracle_ingest",
            "arguments": {
                "source": f"pied-piper-live-{round_id[:40]}",
                "extracted": {
                    "items": [item]
                },
            }
        }
    }
    curl_cmd = [
        "ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
        ORACLE_SSH,
        f"curl -sf -X POST http://localhost:8892/mcp/ "
        f"-H 'Content-Type: application/json' "
        f"-H 'Authorization: Bearer {ORACLE_TOKEN}' "
        f"-H 'MCP-Protocol-Version: 2025-03-26' "
        f"-d '{json.dumps(mcp_payload).replace(chr(39), chr(34))}' 2>/dev/null | head -c 500",
    ]

    try:
        result = subprocess.run(
            curl_cmd,
            capture_output=True, text=True, timeout=20
        )
        return result.returncode == 0 and "error" not in result.stdout.lower()[:100]
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"[ingest-to-oracle] SSH error: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Ingest filtered events to Oracle + local DB")
    ap.add_argument("--in", dest="input", default="-", help="Input NDJSON (default: stdin)")
    ap.add_argument("--skip-oracle", action="store_true", help="Skip Oracle ingest (local DB only)")
    ap.add_argument("--batch", type=int, default=0, help="Stop after N events (0=all)")
    args = ap.parse_args()

    skip_oracle = SKIP_ORACLE or args.skip_oracle
    infile = open(args.input, encoding="utf-8") if args.input != "-" else sys.stdin
    conn = open_db()

    new_count = skip_count = oracle_ok = oracle_fail = 0
    batch_limit = args.batch if args.batch > 0 else float("inf")

    try:
        for raw in infile:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            is_new = upsert_event(conn, event)
            if not is_new:
                skip_count += 1
                continue

            new_count += 1
            print(f"[{new_count}] {event['id'][:50]} [{event.get('channel','?')}]", file=sys.stderr)

            if not skip_oracle:
                ok = ingest_to_oracle_ssh(event)
                if ok:
                    mark_oracle_sent(conn, event["id"])
                    oracle_ok += 1
                else:
                    oracle_fail += 1

            if new_count >= batch_limit:
                print(f"[ingest] Batch limit {batch_limit} reached, stopping.", file=sys.stderr)
                break
    finally:
        if args.input != "-":
            infile.close()
        conn.close()

    print(
        f"[ingest-to-oracle] new={new_count} skipped={skip_count} "
        f"oracle_ok={oracle_ok} oracle_fail={oracle_fail}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
