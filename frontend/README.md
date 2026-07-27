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
`NEXT_PUBLIC_BACKEND_URL` selects how the browser reaches the backend:

- **Set** to an absolute origin → the browser calls the backend directly there (its port
  must match the backend `HTTP_PORT`, and the origin must be in the backend `FRONTEND_URL`).
  Use for local dev and tunnelled setups.
- **Empty** → same-origin mode: REST is relative (`/api/…`) and the WebSocket base comes
  from `window.location`, so the browser only talks to the host that served the page and a
  reverse proxy path-routes `/api` + `/ws` to the backend. The image is then domain/scheme-
  agnostic — no rebuild when the host or http/https changes.

Local dev needs an absolute value (there's no proxy on `:3789`); a proxied deployment is
built with it empty. It's baked at build time, so changing it in absolute mode needs a rebuild.
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
frontend image build. Build it **empty** for a same-origin deployment behind a reverse
proxy (recommended — no rebuild when the domain/scheme changes), or set it to an absolute
browser-visible origin for a direct/tunnelled deployment (rebuild whenever it changes).
