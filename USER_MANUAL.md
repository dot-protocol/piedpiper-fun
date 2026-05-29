# USER_MANUAL.md — piedpiper-fun

Static landing site for [Pipernet](https://github.com/dot-protocol/pipernet) deployed at `https://piedpiper.fun`. Plain HTML, no build step. One Vercel Python serverless function exposes the Track B v0.3 compression engine for live demos. Routes `/chat` and `/chat/*` proxy to the piperchat relay on the VPS.

---

## What it is

A plain-HTML marketing + protocol front door for the Pied Piper / Pipernet ecosystem. It is **not** the protocol; the protocol lives at `github.com/dot-protocol/pipernet`. This repo is the public face, the compression demo API, and the DOT pact document.

---

## Local development

```bash
git clone https://github.com/dot-protocol/piedpiper-fun
cd piedpiper-fun
python3 -m http.server 4000
# open http://localhost:4000
```

No build step. Serves static files directly. The `/api/compress` serverless function is Vercel-specific — it returns 404 locally unless you use `vercel dev`.

### With the compression API locally

```bash
npm i -g vercel
vercel dev
# → http://localhost:3000 (Vercel dev mode, /api/compress live)
```

---

## Deploy

Auto-deploys to `https://piedpiper.fun` on push to `main` via Vercel git integration. Every commit is a production deploy. Pull requests get preview URLs automatically.

```bash
git push origin main
# → piedpiper.fun updates within ~30 seconds
```

---

## File structure

| Path | Purpose |
|---|---|
| `index.html` | Landing page |
| `index-v2.html`, `index-v3.html` | Earlier landing versions |
| `dot.md` | The protocol pact — serves with `Content-Type: text/markdown` |
| `vercel.json` | Routing, rewrites, headers, Python runtime config |
| `api/compress.py` | Serverless function — Track B v0.3 live compression |
| `lab/` | Live compression demo (calls `/api/compress`) |
| `demo/` | Static comparison artifacts |
| `send-preview/` | Service-worker-backed send preview |
| `whitepaper/` | Protocol whitepaper |
| `whitepaper-v2-source.md` | Whitepaper source |
| `data/` | JSON data files (CORS-enabled, 60 s cache) |
| `agents/erlich.html` | Erlich CMO agent page |
| `dashboard/`, `holders/`, `stats/` | Ecosystem info pages |
| `grove/`, `live/` | Additional content sections |
| `vendor/` | Vendored client-side dependencies |

---

## Routing (vercel.json)

| Source | Destination | Type |
|---|---|---|
| `/` | `/chat` | Redirect (temporary) |
| `/chat`, `/chat/*` | `http://69.62.114.97/chat-v15/` and `/*` | Rewrite (VPS proxy) |
| `/c`, `/c/*` | `https://qr-chat-kohl.vercel.app/` and `/*` | Redirect |
| `cleanUrls: true` | `.html` extension stripped from all paths | Built-in |

`/dot.md` is served with `Content-Type: text/markdown; charset=utf-8`.
`/data/*.json` files are served with `Access-Control-Allow-Origin: *` and 60 s cache.

---

## Compression API

### POST /api/compress

Runs the Track B v0.3 arithmetic coder (order-3 Markov + multi-window match + multiplicative mix) on a UTF-8 string.

**Request:**

```bash
curl -X POST https://piedpiper.fun/api/compress \
  -H "Content-Type: application/json" \
  -d '{"text": "the quick brown fox jumps over the lazy dog"}'
```

**Response:**

```json
{
  "raw_bytes": 43,
  "wire_bytes": 38,
  "ratio": 1.1316,
  "bpb": 7.0698,
  "round_trip_ok": true,
  "encoder": "track-b v0.3 (order-3 Markov + multi-window match, multiplicative mix)",
  "windows": [3, 5, 8, 12],
  "encode_ms": 12,
  "source": "https://github.com/dot-protocol/pipernet"
}
```

**Optional parameter:**

```json
{"text": "...", "windows": [3, 5, 8, 12]}
```

`windows` controls which match-model window sizes are mixed in (integers, default `[3, 5, 8, 12]`).

**Limits:**
- Max input: 64 KB (`MAX_INPUT_BYTES = 65536`)
- Max duration: 30 s (Vercel function timeout)
- CORS: `Access-Control-Allow-Origin: *`

**Error response:**

```json
{"error": "...", "type": "ValueError"}
```

HTTP 400 on any validation error.

---

## Observing state

```bash
# Site is up
curl -o /dev/null -s -w "%{http_code}" https://piedpiper.fun/
# → 302 (redirects to /chat)

curl -o /dev/null -s -w "%{http_code}" https://piedpiper.fun/dot.md
# → 200

# Compression API alive
curl -s -X POST https://piedpiper.fun/api/compress \
  -H "Content-Type: application/json" \
  -d '{"text":"hello"}' | python3 -m json.tool

# Vercel deployment status
vercel ls --prod

# Preview deployments
vercel ls
```

---

## Security headers (applied to all routes)

```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
```

`/send-preview/*` additionally sets `Service-Worker-Allowed: /send-preview/` and `Cache-Control: no-cache` on the service worker JS file.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/compress` returns 404 locally | Vercel Python runtime only | Use `vercel dev` instead of `python3 -m http.server` |
| `/chat` fails or shows VPS error | VPS at 69.62.114.97 is down | Check VPS status; the rewrite is a hard proxy with no fallback |
| Deploy not reflecting push | Vercel webhook may be paused | Check `vercel ls` for latest deployment; trigger manually with `vercel --prod` |
| CORS error on `/data/*.json` | Headers missing | Confirm `vercel.json` has the `data/(.*).json` header block |
| `round_trip_ok: false` in API response | Encoder/decoder mismatch (should not happen) | File an issue at `github.com/dot-protocol/pipernet` |

---

## License

- Site copy and `dot.md`: CC BY 4.0
- Code (including `api/compress.py`): MIT

---

*This repo is a static site + one serverless function. Agents that need the protocol should look at `github.com/dot-protocol/pipernet`. Agents that need the chat relay should look at `github.com/dot-protocol/piperchat`.*
