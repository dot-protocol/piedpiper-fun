# piedpiper.fun

Public landing for [Pipernet](https://github.com/dot-protocol/pipernet) — the open-source, federated, agent-native communication protocol.

## What this repo is

The static site source for `https://piedpiper.fun`. Plain HTML, no build step.

- `index.html` — landing page
- `lab/` — live compression demo (calls `/api/compress`)
- `demo/` — static comparison artifacts
- `api/compress.py` — Vercel Python serverless function exposing the `track-b` v0.3 mixer
- `dot.md` — the protocol pact (free, federated, public-commons name)
- `vercel.json` — routing + Python runtime config

## What this repo is not

This is the **landing source**, not the protocol. The protocol lives at
[`github.com/dot-protocol/pipernet`](https://github.com/dot-protocol/pipernet) — that's where the spec, CLI, and compression engine ship.

## Local development

```bash
# any static server works; using python's stdlib:
python3 -m http.server 4000
open http://localhost:4000
```

The compression API is Vercel-specific and only runs in deploy. Locally `lab/`
will load but the `/api/compress` calls will 404 unless you `vercel dev`.

## Deploying

This repo auto-deploys to `https://piedpiper.fun` on push to `main` via the
Vercel git integration. Every commit is a deploy; preview URLs land on PRs.

## License

The site copy and the dot.md pact are CC BY 4.0. Code is MIT.
