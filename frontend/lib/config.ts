/**
 * Backend endpoints — configured entirely from the environment.
 *
 * `NEXT_PUBLIC_BACKEND_URL` (in `frontend/.env.local`) is the single source of truth for
 * the backend origin. Both the HTTP base and the WebSocket base are derived from it —
 * there are no hardcoded host/port fallbacks anywhere.
 */

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL;

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

export function getBackendUrl(): string {
  if (!BACKEND_URL) {
    throw new Error(
      "NEXT_PUBLIC_BACKEND_URL is not set. Define it in frontend/.env.local " +
        "to match the backend URL and HTTP_PORT.",
    );
  }
  return stripTrailingSlash(BACKEND_URL);
}

/** WebSocket base derived from the same env value (http→ws, https→wss). */
export function getBackendWsUrl(): string {
  return getBackendUrl().replace(/^http/, "ws");
}

/** Token used for the `?token=` WS auth query. Any non-empty value is accepted. */
export function getAuthToken(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem("md_access_token") || "anonymous";
}
