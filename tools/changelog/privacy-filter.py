#!/usr/bin/env python3
"""
Component 2: Privacy filter (default deny → allow)

Reads NDJSON events from parse-jsonl.py, applies privacy rules,
assigns channel tags, and emits filtered events.

Usage:
  python parse-jsonl.py <session.jsonl> | python privacy-filter.py [--out filtered.ndjson]

Rules: STRIP (replace with empty), REPLACE (substitute placeholder), ALLOW (pass through).
Default channel: "general" if no heuristic matches.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# STRIP patterns — full match → <REDACTED>
# ---------------------------------------------------------------------------
_STRIP_PATTERNS = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"), "<email>"),
    # Phone numbers (various formats)
    (re.compile(r"\b(\+?1[-.\s]?)?(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b"), "<phone>"),
    # Bearer / API tokens — sk_*, pk_*, hex 32+, JWT (3 base64url segments)
    (re.compile(r"\bsk_[a-zA-Z0-9_-]{16,}\b"), "<token>"),
    (re.compile(r"\bpk_[a-zA-Z0-9_-]{16,}\b"), "<token>"),
    # Long hex strings (32+ chars) — API keys, hashes, secrets
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<token>"),
    # Long alphanumeric+dash tokens (32+ chars, Bearer-style)
    (re.compile(r"\b[a-zA-Z0-9]{8,}(?:-[a-zA-Z0-9]{4,}){3,}[a-zA-Z0-9-]{4,}\b"), "<token>"),
    # Long base64url strings without spaces (32+ chars with mixed case + symbols)
    (re.compile(r"\b[a-zA-Z0-9_.\-]{48,}\b"), "<token>"),
    # JWT pattern (header.payload.signature — three base64url segments)
    (re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"), "<jwt>"),
    # Generic "Bearer <value>" patterns
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_.\-]{20,}"), "Bearer <token>"),
    # Anthropic/OpenAI key patterns
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"), "<token>"),
    (re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"), "<token>"),
    # Wallet addresses (Solana base58, ~44 chars; Ethereum 0x+40 hex)
    # Note: $PIPER mint is allowed — don't strip it specifically
    (re.compile(r"\b0x[0-9a-fA-F]{40}\b"), "<wallet>"),
    (re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{43,44}\b"), "<wallet>"),
    # Secrets dir paths
    (re.compile(r"/Users/[^/\s]+/\.config/secrets/[^\s\"']+"), "<secrets-path>"),
    # TOTP/MFA secrets
    (re.compile(r"(?i)\b(totp|mfa|otp)[_\s]?(secret|seed|key)[\":\s=]+[A-Z2-7]{16,}\b"), "<mfa-secret>"),
]

# ---------------------------------------------------------------------------
# REPLACE patterns — substitute with placeholder but keep context
# ---------------------------------------------------------------------------
_REPLACE_PATTERNS = [
    # VPS IPs (public IPs — strip for safety)
    (re.compile(r"\b69\.62\.114\.97\b"), "<vps>"),
    (re.compile(r"\b100\.124\.78\.56\b"), "<vps>"),
    (re.compile(r"\b100\.\d+\.\d+\.\d+\b"), "<vps>"),   # Tailscale range
    # SSH host shorthands that expose internal topology
    (re.compile(r"\bssh\s+root@[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b"), "ssh root@<vps>"),
    (re.compile(r"\bssh\s+-[^\s]+\s+root@[0-9.]+\b"), "ssh -o ... root@<vps>"),
    # root@IP standalone (e.g., in rsync commands)
    (re.compile(r"\broot@[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b"), "root@<vps>"),
    # Downloads paths — keep filename, strip dir
    (re.compile(r"/Users/[^/\s]+/Downloads/([^\s\"']+)"), r"<downloads>/\1"),
    # Home dir base paths (don't leak username)
    (re.compile(r"/Users/[a-zA-Z0-9_-]+/(?!Downloads)"), "/Users/<user>/"),
    # Auth tokens in URL query params
    (re.compile(r"(\?|&)(token|auth|key|api_key|access_token)=[a-zA-Z0-9_.\-]{10,}"), r"\1\2=<token>"),
]

# ---------------------------------------------------------------------------
# Channel tag heuristics — first match wins, else "general"
# ---------------------------------------------------------------------------
_CHANNEL_RULES = [
    ("dot-protocol", re.compile(
        r"(?i)(dot\s*(protocol|spec|v4|atom)|packet|identity|transport|DID|wire\s*format|"
        r"pied\s*piper\s*(protocol|mesh|p2p)|signal|DOTdrop|DOTpost|envelope\.py|"
        r"Nokia|Meshtastic|LoRa|iroh|CRDT|Ed25519|AT\s*Protocol|ActivityPub)"
    )),
    ("compression", re.compile(
        r"(?i)(compress|Hutter|bpw|bpb|cmix|context.mix|arithmetic.cod|range.cod|"
        r"Markov|FX2|match.model|track.b|predictor|round.trip|wire.bytes)"
    )),
    ("room", re.compile(
        r"(?i)(Faraday|Maxwell|Hertz|Marconi|Tesla|Shannon|Baran|Kay|Jared|Carr|"
        r"room\s*(agent|voice|mind|simulation|council)|terrace|multi.mind|founding|"
        r"BBM|Silicon\s*Valley|season\s*7|episode)"
    )),
    ("launch", re.compile(
        r"(?i)(tweet|postiz|deploy|vercel|launch|coin|\$PIPER|pump\.fun|mint|"
        r"piedpiper\.fun|domain|DNS|marketing|CMO|audience|announcement)"
    )),
    ("infra", re.compile(
        r"(?i)(Oracle|DOTpost|MCP|agents?|Neo4j|VPS|nginx|pm2|docker|SSH|"
        r"tree_serve|tree\.py|ingest|Hebbian|vector|embedding|sentinel|"
        r"Claude\s*Code|tmux|plist|launchd|cron)"
    )),
    ("meta", re.compile(
        r"(?i)(audio\s*(rule|queue|autoplay)|commandment|charter|protocol\s*change|"
        r"CLAUDE\.md|state\.md|session\s*start|Buzzcheck|buzzcheck)"
    )),
]


def _apply_strip(text: str) -> str:
    for pattern, replacement in _STRIP_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _apply_replace(text: str) -> str:
    for pattern, replacement in _REPLACE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def tag_channel(text: str) -> str:
    for channel, pattern in _CHANNEL_RULES:
        if pattern.search(text):
            return channel
    return "general"


def filter_event(event: dict) -> Optional[dict]:
    """Apply privacy filter and channel tag to a parsed event. Returns filtered event."""
    content = event.get("content", "")

    # Apply strip then replace
    filtered = _apply_strip(content)
    filtered = _apply_replace(filtered)

    # Skip events that became empty after filtering
    if not filtered.strip():
        return None

    channel = tag_channel(filtered)

    return {
        **event,
        "content": filtered,
        "channel": channel,
    }


def main():
    ap = argparse.ArgumentParser(description="Privacy filter for parsed JSONL events")
    ap.add_argument("--out", default="-", help="Output file (default: stdout)")
    ap.add_argument("--in", dest="input", default="-", help="Input NDJSON (default: stdin)")
    args = ap.parse_args()

    infile = open(args.input, encoding="utf-8") if args.input != "-" else sys.stdin
    out = open(args.out, "w", encoding="utf-8") if args.out != "-" else sys.stdout

    total = skipped = 0
    channel_counts: dict[str, int] = {}

    try:
        for raw in infile:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            total += 1
            result = filter_event(event)
            if result is None:
                skipped += 1
                continue

            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            ch = result.get("channel", "general")
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
    finally:
        if args.input != "-":
            infile.close()
        if args.out != "-":
            out.close()

    print(
        f"[privacy-filter] {total} in, {total - skipped} out, {skipped} skipped",
        file=sys.stderr,
    )
    for ch, cnt in sorted(channel_counts.items(), key=lambda x: -x[1]):
        print(f"  {ch}: {cnt}", file=sys.stderr)


if __name__ == "__main__":
    main()
