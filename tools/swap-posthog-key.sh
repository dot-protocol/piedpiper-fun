#!/usr/bin/env bash
# swap-posthog-key.sh — replace the PostHog placeholder key across all pages.
#
# Usage:
#   bash tools/swap-posthog-key.sh phc_your_real_project_key
#
# Idempotent: if the placeholder is already swapped, this is a no-op.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <phc_real_key>" >&2
  exit 1
fi

NEW_KEY="$1"
PLACEHOLDER="phc_PLACEHOLDER_REPLACE_ME"

# Validate format (PostHog project keys start with phc_)
if [[ "$NEW_KEY" != phc_* ]]; then
  echo "warning: key '$NEW_KEY' does not start with 'phc_' — proceeding anyway" >&2
fi

# Find the piedpiper-fun root (script lives in piedpiper-fun/tools/)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ swapping $PLACEHOLDER → $NEW_KEY across $ROOT/**/*.html"

count=0
while IFS= read -r f; do
  if grep -q "$PLACEHOLDER" "$f"; then
    # macOS sed needs backup extension; we delete the backup
    sed -i.bak "s|$PLACEHOLDER|$NEW_KEY|g" "$f"
    rm -f "$f.bak"
    echo "  ✓ $f"
    count=$((count + 1))
  fi
done < <(find "$ROOT" -name '*.html' -type f -not -path '*/node_modules/*' -not -path '*/.vercel/*')

echo "→ swapped $count file(s)"
echo "→ verify: grep -c '$NEW_KEY' \"$ROOT\"/**/*.html"
