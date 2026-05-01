"""
Vercel serverless function — live Track B v0.3 compression.

POST /api/compress
  body: {"text": "<utf-8 string>", "windows": [3,5,8,12] (optional)}
  returns:
    {
      "raw_bytes": int,
      "wire_bytes": int,
      "ratio": float,
      "bpb": float,
      "round_trip_ok": bool,
      "encoder": "track-b v0.3 (order-3 Markov + multi-window match)",
      "windows": [3,5,8,12],
      "encode_ms": int
    }

Uses the same code that lives in github.com/dot-protocol/pipernet under
compression/track-b/. Honest output, real numbers, byte-exact round-trip.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler
from typing import Dict, Tuple

import numpy as np


# ============================================================================
# Inlined arithmetic coder + Markov + Match model + Multi-window mix
# Same logic as github.com/dot-protocol/pipernet compression/track-b/
# Inlined here so the serverless function has no cross-file imports.
# ============================================================================

import struct

PRECISION = 32
WHOLE = 1 << PRECISION
HALF = WHOLE >> 1
QUARTER = WHOLE >> 2

ORDER = 3
ALPHABET = 256
LAPLACE_K = 1


class ArithmeticEncoder:
    def __init__(self):
        self._low = 0
        self._high = WHOLE - 1
        self._bits = []
        self._pending = 0

    def encode_symbol(self, cum_lo, cum_hi, cum_total):
        r = self._high - self._low + 1
        self._high = self._low + (r * cum_hi // cum_total) - 1
        self._low = self._low + (r * cum_lo // cum_total)
        self._normalise()

    def _normalise(self):
        while True:
            if self._high < HALF:
                self._emit(0)
                self._low <<= 1
                self._high = (self._high << 1) | 1
            elif self._low >= HALF:
                self._emit(1)
                self._low = (self._low - HALF) << 1
                self._high = ((self._high - HALF) << 1) | 1
            elif self._low >= QUARTER and self._high < HALF + QUARTER:
                self._pending += 1
                self._low = (self._low - QUARTER) << 1
                self._high = ((self._high - QUARTER) << 1) | 1
            else:
                break

    def _emit(self, bit):
        self._bits.append(bit)
        for _ in range(self._pending):
            self._bits.append(1 - bit)
        self._pending = 0

    def finish(self):
        self._pending += 1
        if self._low < QUARTER:
            self._emit(0)
        else:
            self._emit(1)
        bits = self._bits
        # pack to bytes
        n = len(bits)
        out = bytearray()
        for i in range(0, n, 8):
            byte = 0
            for j in range(8):
                if i + j < n and bits[i + j]:
                    byte |= 1 << (7 - j)
            out.append(byte)
        return bytes(out)


class ArithmeticDecoder:
    def __init__(self, data):
        self._data = data
        self._bit_pos = 0
        self._low = 0
        self._high = WHOLE - 1
        self._code = 0
        for _ in range(PRECISION):
            self._code = (self._code << 1) | self._read_bit()

    def _read_bit(self):
        b = self._bit_pos // 8
        if b >= len(self._data):
            return 0
        bit = (self._data[b] >> (7 - (self._bit_pos % 8))) & 1
        self._bit_pos += 1
        return bit

    def decode_symbol(self, cum, total):
        r = self._high - self._low + 1
        v = ((self._code - self._low + 1) * total - 1) // r
        # binary search cum for v
        lo, hi = 0, ALPHABET
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if cum[mid] > v:
                hi = mid
            else:
                lo = mid
        symbol = lo
        self._high = self._low + (r * cum[symbol + 1] // total) - 1
        self._low = self._low + (r * cum[symbol] // total)
        self._normalise()
        return symbol

    def _normalise(self):
        while True:
            if self._high < HALF:
                self._low <<= 1
                self._high = (self._high << 1) | 1
                self._code = (self._code << 1) | self._read_bit()
            elif self._low >= HALF:
                self._low = (self._low - HALF) << 1
                self._high = ((self._high - HALF) << 1) | 1
                self._code = ((self._code - HALF) << 1) | self._read_bit()
            elif self._low >= QUARTER and self._high < HALF + QUARTER:
                self._low = (self._low - QUARTER) << 1
                self._high = ((self._high - QUARTER) << 1) | 1
                self._code = ((self._code - QUARTER) << 1) | self._read_bit()
            else:
                break


class MarkovModel:
    def __init__(self):
        self._counts: Dict[bytes, np.ndarray] = defaultdict(
            lambda: np.full(ALPHABET, LAPLACE_K, dtype=np.uint32)
        )

    def get_cum(self, ctx):
        counts = self._counts[ctx]
        cum = [0] * (ALPHABET + 1)
        total = 0
        for i in range(ALPHABET):
            cum[i] = total
            total += int(counts[i])
        cum[ALPHABET] = total
        return cum, total

    def update(self, ctx, sym):
        self._counts[ctx][sym] += 1

    def counts(self, ctx):
        return np.asarray(self._counts[ctx], dtype=np.uint32)

    @staticmethod
    def context_of(history, pos):
        return history[max(0, pos - ORDER):pos]


class MatchModel:
    def __init__(self, window=8, max_matches=32):
        self.window = window
        self.max_matches = max_matches
        self._index = defaultdict(list)
        self._prefix = bytearray()

    def predict(self):
        counts = np.full(ALPHABET, LAPLACE_K, dtype=np.uint32)
        if len(self._prefix) >= self.window:
            ctx = bytes(self._prefix[-self.window:])
            for pos in self._index.get(ctx, [])[-self.max_matches:]:
                follow = pos + self.window
                if follow < len(self._prefix):
                    counts[self._prefix[follow]] += 1
        return counts, int(counts.sum())

    def update(self, byte):
        self._prefix.append(byte)
        if len(self._prefix) >= self.window:
            start = len(self._prefix) - self.window
            self._index[bytes(self._prefix[start:start + self.window])].append(start)


def _multi_mix(markov_counts, match_counts_list):
    LAPLACE_FLOOR = 256
    markov_total = int(markov_counts.sum())
    p = markov_counts.astype(np.float64) / markov_total
    for mc in match_counts_list:
        if int(mc.sum()) <= LAPLACE_FLOOR:
            continue
        pm = mc.astype(np.float64) / int(mc.sum())
        p = p * pm
        s = p.sum()
        if s > 0:
            p = p / s
        else:
            p = markov_counts.astype(np.float64) / markov_total
            break
    p = p / p.sum()
    mixed_int = np.maximum(1, np.round(p * 1_000_000).astype(np.uint64))
    cum = [0] * (ALPHABET + 1)
    total = 0
    for i in range(ALPHABET):
        cum[i] = total
        total += int(mixed_int[i])
    cum[ALPHABET] = total
    return cum, total


def _header(n):
    return struct.pack(">I", n)


def _unheader(data):
    if len(data) < 4:
        raise ValueError("too short")
    n = struct.unpack(">I", data[:4])[0]
    return n, data[4:]


def encode_track_b(data, windows=(3, 5, 8, 12)):
    markov = MarkovModel()
    matches = [MatchModel(window=k) for k in windows]
    enc = ArithmeticEncoder()
    history = b""
    for i, byte in enumerate(data):
        markov_counts = markov.counts(MarkovModel.context_of(history, i))
        match_counts_list = [m.predict()[0] for m in matches]
        cum, total = _multi_mix(markov_counts, match_counts_list)
        enc.encode_symbol(cum[byte], cum[byte + 1], total)
        markov.update(MarkovModel.context_of(history, i), byte)
        for m in matches:
            m.update(byte)
        history = (history + bytes([byte]))[-(ORDER + 1):]
    return _header(len(data)) + enc.finish()


def decode_track_b(blob, windows=(3, 5, 8, 12)):
    n, payload = _unheader(blob)
    markov = MarkovModel()
    matches = [MatchModel(window=k) for k in windows]
    dec = ArithmeticDecoder(payload)
    out = bytearray()
    history = b""
    for i in range(n):
        markov_counts = markov.counts(MarkovModel.context_of(history, i))
        match_counts_list = [m.predict()[0] for m in matches]
        cum, total = _multi_mix(markov_counts, match_counts_list)
        sym = dec.decode_symbol(cum, total)
        out.append(sym)
        markov.update(MarkovModel.context_of(history, i), sym)
        for m in matches:
            m.update(sym)
        history = (history + bytes([sym]))[-(ORDER + 1):]
    return bytes(out)


# ============================================================================
# Vercel HTTP handler
# ============================================================================

MAX_INPUT_BYTES = 64 * 1024  # 64 KB cap; encoding is O(n) with high constant


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            req = json.loads(body) if body else {}
            text = req.get("text", "")
            if not isinstance(text, str):
                raise ValueError("'text' must be a string")
            raw = text.encode("utf-8")
            if len(raw) == 0:
                raise ValueError("'text' is empty")
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError(
                    f"'text' too large ({len(raw)} bytes); max {MAX_INPUT_BYTES}"
                )
            windows = tuple(req.get("windows", [3, 5, 8, 12]))

            t0 = time.perf_counter()
            wire = encode_track_b(raw, windows=windows)
            encode_ms = int((time.perf_counter() - t0) * 1000)
            back = decode_track_b(wire, windows=windows)
            round_trip_ok = (back == raw)

            payload = {
                "raw_bytes": len(raw),
                "wire_bytes": len(wire),
                "ratio": round(len(raw) / max(1, len(wire)), 4),
                "bpb": round(len(wire) * 8 / max(1, len(raw)), 4),
                "round_trip_ok": round_trip_ok,
                "encoder": "track-b v0.3 (order-3 Markov + multi-window match, multiplicative mix)",
                "windows": list(windows),
                "encode_ms": encode_ms,
                "source": "https://github.com/dot-protocol/pipernet",
            }
            out = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(out)
        except Exception as e:
            err = json.dumps({"error": str(e), "type": type(e).__name__}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(err)
