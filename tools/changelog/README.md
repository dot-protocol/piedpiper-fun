# Changelog Pipeline — engineering-in-public at /live

End-to-end: Claude Code session JSONL → privacy filter → events.db + Oracle → SSE → piedpiper.fun/live

## Components

| Script | Purpose |
|--------|---------|
| `parse-jsonl.py` | JSONL → NDJSON events (role, content, timestamp, ID) |
| `privacy-filter.py` | Strip PII/tokens, tag channel, default-deny |
| `ingest-to-oracle.py` | Write to events.db (SQLite) + Oracle VPS via SSH |
| `../../api/live.py` | Vercel SSE endpoint reading events.db |
| `../../live/index.html` | /live page (channel filter, SSE, markdown, permalinks) |
| `backfill.sh` | One-shot backfill of existing session JSONL |
| `watcher.py` | Daemon polling JSONL every 30s for incremental ingest |
| `com.piedpiper.watcher.plist` | launchd service definition |

## Run the full pipeline

```bash
cd /Users/blaze/Movies/Kin/piedpiper-fun

# One-shot backfill (idempotent)
bash tools/changelog/backfill.sh

# Skip Oracle, local DB only (faster for testing)
SKIP_ORACLE=1 bash tools/changelog/backfill.sh

# Manual one-shot watch (no daemon)
python3 tools/changelog/watcher.py --once --skip-oracle
```

## Install launchd watcher

```bash
# Copy plist and load
cp tools/changelog/com.piedpiper.watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.piedpiper.watcher.plist
launchctl start com.piedpiper.watcher

# Check status
launchctl list | grep piedpiper

# View logs
tail -f tools/changelog/watcher.log
```

## Deploy to Vercel

The `/api/live.py` endpoint reads `tools/changelog/events.db` which is bundled
at build time. To get live data on Vercel, the events.db must be committed.

**Development workflow:**
1. Run backfill locally to populate events.db
2. Commit events.db to the repo
3. Push to Vercel — /live page will serve the bundled snapshot

**For real-time (future):** Use a Vercel-accessible DB (Vercel KV, PlanetScale, or
write events.db via a cron and commit on schedule).

```bash
# After backfill, deploy
git add tools/changelog/events.db live/ api/live.py
git commit -m "feat(live): engineering-in-public changelog at /live"
# push when ready
```

## Test locally

```bash
# Test parse on the current session JSONL
python3 tools/changelog/parse-jsonl.py \
  ~/.claude/projects/-Users-blaze-Movies-Kin/11fc346e-b4ff-4eef-ad31-a1cee6c0b540.jsonl \
  2>&1 | head -5

# Test privacy filter
python3 tools/changelog/parse-jsonl.py \
  ~/.claude/projects/-Users-blaze-Movies-Kin/11fc346e-b4ff-4eef-ad31-a1cee6c0b540.jsonl \
  | python3 tools/changelog/privacy-filter.py 2>&1

# Query local DB
python3 -c "
import sqlite3; conn = sqlite3.connect('tools/changelog/events.db')
conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT channel, COUNT(*) n FROM events GROUP BY channel ORDER BY n DESC'):
    print(r['channel'], r['n'])
"
```

## Privacy filter rules (summary)

**STRIP → `<redacted>`:** emails, phones, API keys (sk_*, pk_*), hex 32+, JWTs, Ethereum wallets, Solana wallets, secrets dir paths

**REPLACE:** VPS IPs → `<vps>`, tokens in URLs → `<token>`, `/Users/<name>/` → `/Users/<user>/`, Downloads paths → `<downloads>/<filename>`

**ALLOW:** Protocol design, architecture decisions, room voices, compression theory, marketing copy, SV bible quotes, Buzzcheck footers
