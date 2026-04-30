#!/usr/bin/env python3
"""
Real relationship compression measurement.

Encodes a conversation as a SINGLE adaptive arithmetic-coded stream
using middle-out's order-3 Markov model. The model learns from every
byte it sees — so each new message benefits from the accumulated
context of every prior message.

For each message, we measure its MARGINAL cost: the difference in
encoded bytes between (messages 0..i) and (messages 0..i-1). The
shrinking marginal cost per byte demonstrates relationship
compression in real numbers — no interpolation, no mocks.

This is the honest version. The numbers are modest (order-3 + adaptive
Markov is the *seed* model; cmix-class and our context-mixer v1 score
much higher). The architectural curve is the point.
"""

import json
import sys
from pathlib import Path

# Use middle-out's real arithmetic coder + adaptive Markov model
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "middle-out"))
from src.baseline import encode, decode  # noqa: E402

# A real exchange — two people who know each other.
CONVERSATION = [
    ("a", "hi"),
    ("b", "hey — long day?"),
    ("a", "the meeting ran over again. same thing as last week."),
    ("b", "with the same person who never finishes a sentence?"),
    ("a", "yeah. and they brought the deck up again."),
    ("b", "the one from october?"),
    ("a", "that one. third time. nothing has changed in it."),
    ("b", "are you saying the thing tomorrow?"),
    ("a", "yeah. like we said."),
    ("b", "good."),
    ("a", "thanks for last night btw."),
    ("b", "of course. always."),
    ("a", "ok. talk after?"),
    ("b", "yeah."),
]


def stream_for_prefix(idx: int) -> bytes:
    """Concatenate messages [0..idx] into one bytestream with sender prefix."""
    out = bytearray()
    for i in range(idx + 1):
        sender, text = CONVERSATION[i]
        out.extend(f"<{sender}> ".encode("utf-8"))
        out.extend(text.encode("utf-8"))
        out.extend(b"\n")
    return bytes(out)


def main():
    out = []

    # Round-trip the whole conversation first — sanity check
    full = stream_for_prefix(len(CONVERSATION) - 1)
    encoded_full = encode(full)
    assert decode(encoded_full) == full, "round-trip failed on full conversation"

    # 4-byte length header — same for every prefix encoding, so it cancels in diffs
    HEADER_BYTES = 4

    # Encode every prefix once; measure per-message marginal cost
    prev_len = HEADER_BYTES  # the empty stream costs only the header
    cumulative_raw = 0
    cumulative_wire = 0

    for i, (sender, text) in enumerate(CONVERSATION):
        prefix = stream_for_prefix(i)
        encoded = encode(prefix)
        encoded_len = len(encoded)

        # Round-trip this prefix
        assert decode(encoded) == prefix, f"round-trip failed at idx {i}"

        # The chunk this message contributed to the raw stream
        sender_marker = f"<{sender}> ".encode("utf-8") + b"\n"
        message_bytes = text.encode("utf-8")
        raw_chunk_size = len(sender_marker) + len(message_bytes) - 1  # \n once
        # (we counted "\n" inside sender_marker; subtract one because there's only one \n total)

        # Marginal wire cost
        delta = encoded_len - prev_len
        # Encoder finish flushes some pending bits at end-of-stream; per-message
        # delta naturally absorbs that as the stream extends. Honest measurement.

        ratio = raw_chunk_size / max(1, delta) if delta > 0 else None

        # Standalone cost (compressing this message alone for comparison)
        alone_encoded = encode(message_bytes)
        alone_len = len(alone_encoded)
        ratio_alone = len(message_bytes) / max(1, alone_len)

        out.append({
            "i": i,
            "from": sender,
            "text": text,
            "raw": raw_chunk_size,
            "wire": delta,
            "alone": alone_len,
            "ratio": round(ratio, 3) if ratio else None,
            "ratio_alone": round(ratio_alone, 3),
        })

        cumulative_raw += raw_chunk_size
        cumulative_wire += delta
        prev_len = encoded_len

    # Cumulative truth on the whole conversation
    final_full_encoded = encode(full)
    cum_ratio = len(full) / max(1, len(final_full_encoded))

    # Cost of every message compressed independently
    independent_total = sum(len(encode(text.encode("utf-8"))) for _, text in CONVERSATION)
    indep_ratio = sum(len(t.encode()) for _, t in CONVERSATION) / max(1, independent_total)

    payload = {
        "method": "middle-out v0 baseline — order-3 Markov model + arithmetic coding (single stream)",
        "library": f"python {sys.version.split()[0]}",
        "honest": True,
        "round_trip_verified": True,
        "messages": out,
        "totals": {
            "messages": len(CONVERSATION),
            "raw_full_bytes": len(full),
            "wire_full_bytes": len(final_full_encoded),
            "ratio_with_relationship": round(cum_ratio, 3),
            "wire_independent_bytes": independent_total,
            "ratio_independent": round(indep_ratio, 3),
            "lift_from_relationship": round(cum_ratio / indep_ratio, 3),
        },
        "notes": [
            "Single arithmetic stream — Markov model learns from every prior byte.",
            "Marginal wire cost per message ≈ (encoded[0..i]) − (encoded[0..i-1]).",
            "Order-3 Markov is the seed model; production uses 6+ predictors with logistic mixing.",
            "Numbers shown are modest by design. The CURVE is the architectural truth.",
        ],
    }

    target = Path(__file__).parent / "data.json"
    target.write_text(json.dumps(payload, indent=2))

    # Concise summary on stderr
    t = payload["totals"]
    print(json.dumps(payload, indent=2))
    print("\n--- summary ---", file=sys.stderr)
    print(f"  full conversation: {t['raw_full_bytes']}B raw → {t['wire_full_bytes']}B wire "
          f"(ratio {t['ratio_with_relationship']:.2f} : 1)", file=sys.stderr)
    print(f"  independent encode: {t['wire_independent_bytes']}B "
          f"(ratio {t['ratio_independent']:.2f} : 1)", file=sys.stderr)
    print(f"  relationship lift: {t['lift_from_relationship']:.2f}x improvement vs independent",
          file=sys.stderr)
    print(f"  written: {target}", file=sys.stderr)


if __name__ == "__main__":
    main()
