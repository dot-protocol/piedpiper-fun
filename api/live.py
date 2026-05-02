"""
Component 4: Live SSE endpoint — /api/live

Streams events from the local events.db (populated by ingest-to-oracle.py).
CORS-enabled, Vercel serverless-compatible (Python handler pattern).

GET /api/live
  ?channel=all|dot-protocol|launch|room|compression|infra|meta|general
  ?since_id=<event_id>   — return only events newer than this (for polling)
  ?format=sse            — SSE stream (default)
  ?format=json           — return last 50 events as JSON array (for initial load)

SSE events:
  event: event
  data: {id, session_id, timestamp_ms, timestamp_iso, role, content, channel, tool_uses_count}

  event: ping
  data: {}
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Path to the SQLite events store (relative to this file when deployed to Vercel)
# Vercel: /var/task/tools/changelog/events.db (bundled read-only)
# Local:  ../tools/changelog/events.db

_HERE = Path(__file__).parent
_DB_CANDIDATES = [
    _HERE / ".." / "tools" / "changelog" / "events.db",
    Path("/var/task/tools/changelog/events.db"),
    Path("/tmp/events.db"),
]


def _db_path() -> str | None:
    for p in _DB_CANDIDATES:
        if p.exists():
            return str(p.resolve())
    return None


def _get_events(channel: str = "all", since_id: str = "", limit: int = 50) -> list[dict]:
    db = _db_path()
    if not db:
        return []
    try:
        conn = sqlite3.connect(db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if channel and channel != "all":
            if since_id:
                # Get timestamp_ms of since_id for cursor pagination
                row = conn.execute(
                    "SELECT timestamp_ms, msg_index FROM events WHERE id=?", (since_id,)
                ).fetchone()
                if row:
                    rows = conn.execute(
                        "SELECT * FROM events WHERE channel=? AND (timestamp_ms > ? OR (timestamp_ms=? AND msg_index > ?)) "
                        "ORDER BY timestamp_ms ASC, msg_index ASC LIMIT ?",
                        (channel, row["timestamp_ms"], row["timestamp_ms"], row["msg_index"], limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM events WHERE channel=? ORDER BY timestamp_ms ASC, msg_index ASC LIMIT ?",
                        (channel, limit),
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events WHERE channel=? ORDER BY timestamp_ms DESC, msg_index DESC LIMIT ?",
                    (channel, limit),
                ).fetchall()
        else:
            if since_id:
                row = conn.execute(
                    "SELECT timestamp_ms, msg_index FROM events WHERE id=?", (since_id,)
                ).fetchone()
                if row:
                    rows = conn.execute(
                        "SELECT * FROM events WHERE (timestamp_ms > ? OR (timestamp_ms=? AND msg_index > ?)) "
                        "ORDER BY timestamp_ms ASC, msg_index ASC LIMIT ?",
                        (row["timestamp_ms"], row["timestamp_ms"], row["msg_index"], limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM events ORDER BY timestamp_ms DESC, msg_index DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY timestamp_ms DESC, msg_index DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "timestamp_ms": r["timestamp_ms"],
                "timestamp_iso": r["timestamp_iso"],
                "role": r["role"],
                "content": r["content"][:4000],  # cap for SSE payload
                "channel": r["channel"],
                "tool_uses_count": r["tool_uses"],
                "msg_index": r["msg_index"],
            })
        conn.close()
        return result
    except Exception as e:
        return [{"error": str(e)}]


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def log_message(self, *args):
        pass  # silence default access logging

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        channel = params.get("channel", ["all"])[0]
        since_id = params.get("since_id", [""])[0]
        fmt = params.get("format", ["sse"])[0]

        # JSON mode — quick REST for initial page load
        if fmt == "json":
            events = _get_events(channel=channel, since_id=since_id, limit=50)
            body = json.dumps({"events": events, "count": len(events)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return

        # SSE mode — stream events with long-poll heartbeat
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()

        def _sse(event_type: str, data: dict):
            payload = (
                f"event: {event_type}\n"
                f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            )
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        try:
            # Send existing events first (newest last = chronological for SSE)
            events = _get_events(channel=channel, since_id=since_id, limit=50)
            events_asc = sorted(events, key=lambda e: (e.get("timestamp_ms", 0), e.get("msg_index", 0)))
            for ev in events_asc:
                _sse("event", ev)

            last_id = events_asc[-1]["id"] if events_asc else since_id

            # Poll for new events every 3s (Vercel function max 30s, so ~9 iterations)
            for _ in range(9):
                time.sleep(3)
                new_events = _get_events(channel=channel, since_id=last_id, limit=20)
                new_asc = sorted(new_events, key=lambda e: (e.get("timestamp_ms", 0), e.get("msg_index", 0)))
                for ev in new_asc:
                    _sse("event", ev)
                if new_asc:
                    last_id = new_asc[-1]["id"]

            # Heartbeat at end to signal client to reconnect
            _sse("ping", {"ts": int(time.time() * 1000)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                _sse("error", {"error": str(e)})
            except Exception:
                pass
