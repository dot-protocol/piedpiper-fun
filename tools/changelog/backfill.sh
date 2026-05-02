#!/usr/bin/env bash
# Component 6: Backfill existing 16.9 MB JSONL session into events.db + Oracle
# Idempotent — safe to re-run; already-ingested events are skipped.
#
# Usage:
#   cd /Users/blaze/Movies/Kin/piedpiper-fun
#   bash tools/changelog/backfill.sh [JSONL_PATH] [--skip-oracle]
#
# Default JSONL: ~/.claude/projects/-Users-blaze-Movies-Kin/<latest>.jsonl

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECTS_DIR="$HOME/.claude/projects/-Users-blaze-Movies-Kin"
SKIP_ORACLE="${SKIP_ORACLE:-0}"

# Parse args
JSONL_PATH=""
for arg in "$@"; do
  case "$arg" in
    --skip-oracle) SKIP_ORACLE=1 ;;
    *.jsonl)       JSONL_PATH="$arg" ;;
  esac
done

# Auto-detect latest JSONL if not specified
if [ -z "$JSONL_PATH" ]; then
  JSONL_PATH=$(ls -t "$PROJECTS_DIR"/*.jsonl 2>/dev/null | head -1)
  if [ -z "$JSONL_PATH" ]; then
    echo "[backfill] ERROR: No .jsonl files found in $PROJECTS_DIR" >&2
    exit 1
  fi
fi

echo "[backfill] Session: $JSONL_PATH"
echo "[backfill] Skip Oracle: $SKIP_ORACLE"
echo "[backfill] DB: $HERE/events.db"
echo ""

SKIP_FLAG=""
if [ "$SKIP_ORACLE" = "1" ]; then
  SKIP_FLAG="--skip-oracle"
fi

python3 "$HERE/parse-jsonl.py" "$JSONL_PATH" \
  | python3 "$HERE/privacy-filter.py" \
  | python3 "$HERE/ingest-to-oracle.py" $SKIP_FLAG

echo ""
echo "[backfill] Done."

# Print channel distribution from DB
python3 - <<'PYEOF'
import sqlite3, os
db = os.path.join(os.path.dirname(__file__) if '__file__' in dir() else '.', 'events.db')
# Walk from script dir
import sys
db = os.path.join(os.path.dirname(sys.argv[0]) if sys.argv[0] != '-c' else '.', 'events.db')
# Resolve relative to this script
db_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'events.db') if sys.argv[0] != '-c' else 'events.db'
if not os.path.exists(db_path):
    # Try relative
    db_path = os.path.join(os.getcwd(), 'tools/changelog/events.db')
if not os.path.exists(db_path):
    print("[backfill] DB not found for stats")
    sys.exit(0)
conn = sqlite3.connect(db_path)
total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
print(f"\n[backfill] Total events in DB: {total}")
rows = conn.execute("SELECT channel, COUNT(*) as n FROM events GROUP BY channel ORDER BY n DESC").fetchall()
for ch, n in rows:
    bar = '█' * min(n // 5, 40)
    print(f"  {ch:<16} {n:>4}  {bar}")
conn.close()
PYEOF
