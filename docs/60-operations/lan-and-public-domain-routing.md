# LAN & public domain routing

How to make TickVault reachable by name — first on a private LAN/tailnet, later on the
public internet — and the codebase changes that supports it. This captures the design
discussion and the decisions taken.

## The decisive fact: the browser talks to the backend (Case B)

The frontend is a Next.js app. Its backend origin comes from `NEXT_PUBLIC_BACKEND_URL`,
which Next **inlines into the browser JavaScript at build time** (see `frontend/Dockerfile`
— it is a build `ARG`, not a runtime env). Two things confirm the browser (not the
frontend server) calls the backend directly:

- `frontend/lib/api.ts` is a `"use client"` module; every REST call
  (`fetch(`${getBackendUrl()}${path}`)`) runs in the browser.
- `frontend/lib/wsTopicConnection.ts` opens `new WebSocket(`${getBackendWsUrl()}/ws/…`)`
  — a WebSocket can only be created client-side — and there is **no** proxy in
  `next.config.ts`.

All backend consumers (`login`, `monitor`, `stocks`, `option-chain` pages, `SessionBadge`,
`ConnectionDot`, `NavBar`) are `"use client"`. There are **no server-side backend fetches**.

Consequence: no DNS/proxy trick alone can route the app, because a hostname change does not
touch a URL already compiled into the JS the phone downloads.

## Chosen architecture: single host, same-origin, path-based routing

Expose **one** hostname; the reverse proxy splits by path:

```
tickvault.<domain>/           → frontend:3789   (pages, /_next static, /login, /monitor…)
tickvault.<domain>/api/*      → backend:9000
tickvault.<domain>/ws/*       → backend:9000    (WebSocket upgrade enabled)
```

The browser only ever knows the one origin; `/api` and `/ws` are forwarded to the backend
**inside the Docker network by container name**. The backend needs **no public and no LAN
endpoint of its own** on either network — frontend and backend share the
`data-downloader` default network, and the reverse proxy is the only exposed surface.

This is superior to a two-hostname (`api.tickvault.*`) scheme because it keeps the backend
private and, combined with the code change below, makes the frontend image
**domain- and scheme-agnostic** (no rebuild when the hostname or http/https changes).

### Why not Next.js rewrites
Next `rewrites()` cannot proxy WebSocket upgrades, and `/ws/*` is required. The reverse
proxy does the path split instead, so `next.config.ts` stays unchanged.

## Codebase changes for same-origin support — IMPLEMENTED

1. ✅ `frontend/lib/config.ts` — when `NEXT_PUBLIC_BACKEND_URL` is empty/unset:
   - `getBackendUrl()` returns `""` so `apiFetch` issues **same-origin relative** requests
     (`fetch("/api/…")`).
   - `getBackendWsUrl()` derives from the page: `(https→wss, else ws)://window.location.host`
     (guarded with `typeof window !== "undefined"`; only called client-side).
   - The absolute-URL path still works when the env var **is** set (dev/tunnel).
2. ✅ `frontend/Dockerfile` — the hard `RUN test -n "${NEXT_PUBLIC_BACKEND_URL}"` guard is
   removed, so an empty value (same-origin mode) builds.
3. ✅ Build pipeline unblocked for an empty value:
   - `compose.yaml` frontend build arg `${NEXT_PUBLIC_BACKEND_URL:?…}` → `${NEXT_PUBLIC_BACKEND_URL-}`.
   - `release_manager/lib/common.sh` `image_build_config_hash` no longer rejects an empty
     `NEXT_PUBLIC_BACKEND_URL` (empty is a valid, deterministic same-origin build identity).
4. Backend runtime `.env` — `FRONTEND_URL` **must include the browser origin**. This is the
   allow-list for both CORS and the WebSocket `Origin` check in `app/ws/routes.py`. It is
   comma-separated, so both can coexist, e.g.
   `FRONTEND_URL=http://localhost:3789,http://tickvault.beonedge.internal`.
   Runtime only — edit `.env`, restart backend; no rebuild.
5. ✅ Docs/templates updated (`frontend/.env.local.example`, `frontend/README.md`).

**Build vs runtime env (two separate files):** the *build-time* `NEXT_PUBLIC_BACKEND_URL`
comes from `frontend/.env.local` on the build machine (baked into the image); the deployed
stack's `.env` (`/srv/dev_stack/DATA_DOWNLOADER/.env`) is *runtime* only (`FRONTEND_URL`,
ports, secrets). For a same-origin image, build with `NEXT_PUBLIC_BACKEND_URL` empty; keep
`frontend/.env.local` absolute only for local `next dev` (which has no proxy).

**Rebuild reality:** one rebuild to ship the same-origin image; after that it's domain-
agnostic and future domain/scheme changes need **zero** rebuilds.

### Path-collision caveat
Both the frontend and backend serve `/monitor`. Route **only** `/api` and `/ws` to the
backend — never `/monitor` (it would shadow the frontend page). `/api` does not collide
(there is no `frontend/app/api` route).

## Phase 1 — private LAN / tailnet (`.internal`)

- **Name:** `tickvault.beonedge.internal`. `.internal` is ICANN's reserved private-use TLD
  (2024) and is cleaner than `.local` (no mDNS/Bonjour ambiguity).
- **Hard limit:** `.internal` never resolves on the public internet and public CAs
  (Let's Encrypt) will **not** issue certs for it. For TLS you need a **private CA**
  (`mkcert` / `step-ca` / NPM self-signed) that your devices trust, or run plain `http://`
  internally.
- **DNS:** an internal resolver (e.g. AdGuard Home) rewrites `*.beonedge.internal` →
  the host IP; the reverse proxy (e.g. Nginx Proxy Manager) does the path routing above.
  AdGuard/`.internal` are only needed for this private phase.
- Tailscale here is only the **admin/SSH path** to the VPS, not how end-user devices reach
  the app.

## Phase 2 — public internet via static IP (~within 45 days)

Thanks to same-origin, most of this is ops, not code:

1. **Real public domain** (e.g. `tickvault.beonedge.com`); public DNS `A` record → static
   IP. Drop `.internal`/AdGuard — public DNS handles resolution. Do **not** expose a DNS
   resolver publicly.
2. **TLS/HTTPS** on the proxy (Let's Encrypt), 443, 80→443 redirect, HSTS. `wss://` is
   auto-derived by `config.ts` — **no rebuild**.
3. **`FRONTEND_URL` = the `https://` origin** (runtime).
4. **Keep the app private; expose only the proxy.** Leave `HOST_BIND_ADDRESS=127.0.0.1`
   (or drop host publishing entirely) and let the proxy reach `backend:9000` /
   `frontend:3789` over the Docker network. Never publish 9000/3789 to `0.0.0.0` on a
   public IP.
5. **Firewall:** allow 443 (+80 redirect) and locked-down SSH (ideally Tailscale-only);
   block everything else.

Optionally use **split-horizon DNS**: one public FQDN, resolved to the static IP on the
internet and to the internal IP on the LAN.

### The mandatory gap before public exposure: authentication

The app is unauthenticated **by design** — `app/ws/routes.py`: *"private VPS access control
is handled by the host network rather than a second application authentication layer."*
That assumption breaks on the public internet.

- The `_require_frontend_origin` checks in `app/api/auth.py` and the WS `Origin` check are
  **CSRF mitigation, not authentication** — the `Origin` header is only honoured by
  browsers; a script/`curl`/`websocat` can send any value and pass straight through.
- Exposed today with no real auth: `GET /api/auth/status` (operational disclosure), the
  login state machine (`/api/auth/login*`, incl. a 6-digit TOTP with no lockout),
  `/api/capture/*`, `/api/stats`, `/api/status`, `/monitor`, and all `/ws/*` market data.

Options, cheapest → most robust:
- **Proxy gate:** NPM Access List (HTTP Basic Auth and/or IP allow-list) on the host.
- **App-level auth:** cookie/session login gating `/api` + `/ws` (needs `Secure`+`SameSite`
  over HTTPS) with a frontend login screen.
- **Identity proxy:** Cloudflare Access / Authelia / oauth2-proxy in front of everything.

Decide this **before** the public cutover.

### Hardening for public
Rate-limiting / fail2ban (especially login routes), TOTP lockout, security headers,
request-size limits, logging/alerting.

## Kite static IP — NOT this project's concern

Per Zerodha (Oct 2025) the SEBI static-IP mandate (effective **1 Apr 2026**) applies **only
to order-placement endpoints**. Market-data/WebSocket streaming, login, quotes, instruments,
and token validation are unaffected. Token fetch/validation goes through **calspread.online**
(our own AWS-hosted platform), which never required a static IP.

Therefore:
- **TickVault (this project) does not need a static IP.** It only downloads market data and
  serves it. The `KITE_STATIC_IP` / `KITE_HTTP_PROXY` settings and their egress-binding
  code have been **removed** from this project.
- The static-IP requirement belongs to **`algo_engine`** — the separate, upcoming
  Rust order-placement service, whose *outbound* egress IP must be whitelisted with Kite.

### Data boundary to `algo_engine`
TickVault streams market data to `algo_engine` over **ZMQ**. As both run on the same
machine, keep that link **internal only** (an `ipc://` socket, or `tcp://` bound to the
Docker network / loopback) — it carries no auth and must never be published to the LAN or
the public internet. `algo_engine` therefore gets its market data from TickVault (not a
second Kite WebSocket) and needs Kite only for orders — exactly what the static IP covers.

## Summary

- The app is **Case B** (browser ↔ backend), so name-based routing needs the same-origin
  code change, not just DNS/proxy.
- One host, path-routed; backend stays private on the Docker network on both LAN and public.
- `.internal` for the private phase (private-CA TLS); a real domain + LE TLS + auth for the
  public phase.
- Static IP is `algo_engine`'s concern; it has been removed from TickVault.
