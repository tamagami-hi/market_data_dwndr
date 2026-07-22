# market_data_dwndr — frontend

Next.js 16 (App Router) + React 19 + Tailwind v4 dashboard for the capture backend.
Ported from `algo_engine/frontend_stack` and trimmed to capture-only, wired to the
backend's tagged-envelope WebSocket protocol (`app/ws/protocol.py`).

## Pages

| Route | Topic(s) | Shows |
|-------|----------|-------|
| `/monitor` | `capture-status`, `session` + `/api/capture/history` | Per-underlying WS health, frames written, file size, 1 Hz heartbeat, unmatched, global telemetry, and cumulative per-session live/archive download history. |
| `/login` | `/api/auth/status` | Automatic token-broker fetch/validation and downloader-initialization progress; no manual input (the risk-free rate is fetched daily). |
| `/option-chain` | `market-data` | ATM ± 50 index chains with reconstructed IV & Greeks; spot / ATM / max-pain markers; keyframe + delta patching; index selector. |
| `/stocks` | `stocks` | F&O board matrix: spot + up to 3 nearest futures with live & daily calendar spreads; symbol filter. |

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local   # set the frontend and backend URLs for your environment
npm run dev
```

### Port

The serving port comes from **`PORT` in `.env.local`** — no port is hardcoded in the
scripts. `npm run dev` / `npm run start` load `.env.local` via `dotenv-cli`
(`dotenv -e .env.local -- next …`) so `PORT` takes effect:

```
# frontend/.env.local
NEXT_PUBLIC_BACKEND_URL=http://localhost:<backend-port>
PORT=<frontend-port>
E2E_FRONTEND_PORT=<unused-test-port>
```

To change the port, edit `PORT` and restart the frontend; no code change is needed.
Set `NEXT_PUBLIC_BACKEND_URL` to a browser-reachable backend host and the backend's
`.env` `HTTP_PORT`, then restart (or rebuild for production) after changing it.
`npm run test:e2e` uses `E2E_FRONTEND_PORT` so its production server can run alongside
the development server; that port is also read only from `.env.local`.

The frontend does not implement a second operator-authentication layer. Keep both services
bound to loopback, Tailscale, or another trusted private network; `FRONTEND_URL` remains
the backend allow-list for browser HTTP and WebSocket origins.

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
  config.ts             backend URL / WS URL
  wsTopicConnection.ts  ref-counted per-topic WebSocket (reconnect/backoff)
  wsTypes.ts            tagged-envelope message + payload types
  useTopic.ts           React hooks (useTopicEnvelopes, useConnectionState)
  numberFormat.ts       en-IN formatting helpers
```

The backend must be running with capture active to stream live data; otherwise pages
render their connection state and "waiting for data" placeholders.

For production Docker deployment, `NEXT_PUBLIC_BACKEND_URL` is embedded during the
frontend image build. Update `frontend/.env.local` and rebuild the frontend whenever
that browser-visible origin changes.
