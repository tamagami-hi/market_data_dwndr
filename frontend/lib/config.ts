/**
 * Backend endpoints — configured from the environment, with a same-origin default.
 *
 * `NEXT_PUBLIC_BACKEND_URL` (baked at build time) selects the mode:
 *
 * - **Set** (e.g. `http://localhost:9000`) — ABSOLUTE mode. All REST + WS URLs target
 *   that origin directly. Used for local dev (`next dev`, frontend and backend on
 *   separate ports) and for an SSH-tunnel / explicit-host deployment.
 *
 * - **Empty / unset** — SAME-ORIGIN mode. REST calls become relative (`/api/…`) and the
 *   WebSocket base is derived from the page's own `window.location`. The browser then
 *   only ever talks to the origin that served the page, and a reverse proxy path-routes
 *   `/api` + `/ws` to the backend. This makes the built image domain- and scheme-
 *   agnostic: renaming the host or switching http↔https needs no rebuild.
 */

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL;

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

/**
 * HTTP base for backend calls.
 *
 * Returns the configured absolute origin, or `""` in same-origin mode so callers issue
 * relative requests (`fetch("/api/…")`) that resolve against the current page origin.
 */
export function getBackendUrl(): string {
  if (!BACKEND_URL) {
    return ""; // same-origin: relative URLs resolve against the page's origin
  }
  return stripTrailingSlash(BACKEND_URL);
}

/**
 * WebSocket base (no trailing path).
 *
 * - Absolute mode: derived from `NEXT_PUBLIC_BACKEND_URL` (`http`→`ws`, `https`→`wss`).
 * - Same-origin mode: derived from `window.location`, so the scheme/host/port match the
 *   page exactly (`https:`→`wss:`, otherwise `ws:`). Only ever invoked client-side (from
 *   the WS connect path); returns `""` under SSR/build where `window` is absent.
 */
export function getBackendWsUrl(): string {
  const base = getBackendUrl();
  if (base) {
    return base.replace(/^http/, "ws");
  }
  if (typeof window !== "undefined") {
    const wsScheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsScheme}//${window.location.host}`;
  }
  return ""; // SSR/build: a WebSocket is only ever opened in the browser
}
