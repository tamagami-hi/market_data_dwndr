# market_data_dwndr — frontend

Next.js 16 (App Router) + React 19 + Tailwind v4 dashboard for the capture backend.
Ported from `algo_engine/frontend_stack` and trimmed to capture-only, wired to the
backend's tagged-envelope WebSocket protocol (`app/ws/protocol.py`).

## Pages

| Route | Topic(s) | Shows |
|-------|----------|-------|
| `/monitor` | `capture-status`, `session` | Per-underlying WS health, frames written, file size, 1 Hz heartbeat, unmatched, plus global tokens / fps / disk usage and a session log. |
| `/option-chain` | `market-data` | ATM ± 50 index chains with reconstructed IV & Greeks; spot / ATM / max-pain markers; keyframe + delta patching; index selector. |
| `/stocks` | `stocks` | F&O board matrix: spot + up to 3 nearest futures with live & daily calendar spreads; symbol filter. |

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_BACKEND_URL if backend isn't on :8000
npm run dev                          # http://localhost:3000
```

### Port

The serving port comes from **`PORT` in `.env.local`** — no port is hardcoded in the
scripts. `npm run dev` / `npm run start` load `.env.local` via `dotenv-cli`
(`dotenv -e .env.local -- next …`) so `PORT` takes effect:

```
# frontend/.env.local
NEXT_PUBLIC_BACKEND_URL=http://localhost:9000
PORT=3000
```

To change the port (e.g. if you hit `EADDRINUSE: address already in use :::3000`,
which means something is already listening on that port), just edit `PORT` and re-run —
no code change. You can still override for one run with a shell var: `PORT=3001 npm run dev`.

## Build / lint

```bash
npm run build   # next build (Turbopack) — type-checked, all routes prerender
npm run lint    # eslint (flat config, eslint-config-next 16)
```

## Layout

```
app/
  layout.tsx        nav shell
  page.tsx          landing
  monitor/          Capture Monitor
  option-chain/     option chain
  stocks/           F&O board
components/          NavBar, ConnectionDot, OptionChainTable
lib/
  config.ts             backend URL / WS URL / auth token
  wsTopicConnection.ts  ref-counted per-topic WebSocket (reconnect/backoff)
  wsTypes.ts            tagged-envelope message + payload types
  useTopic.ts           React hooks (useTopicEnvelopes, useConnectionState)
  numberFormat.ts       en-IN formatting helpers
```

The backend must be running with capture active to stream live data; otherwise pages
render their connection state and "waiting for data" placeholders.
