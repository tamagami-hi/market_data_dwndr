/** Backend endpoints. Override with NEXT_PUBLIC_BACKEND_URL (e.g. http://host:8000). */

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

export function getBackendUrl(): string {
  const env = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (env) return stripTrailingSlash(env);
  if (typeof window !== "undefined") {
    return stripTrailingSlash(`${window.location.protocol}//${window.location.hostname}:8000`);
  }
  return "http://localhost:8000";
}

export function getBackendWsUrl(): string {
  const http = getBackendUrl();
  return http.replace(/^http/, "ws");
}

/** Token used for the `?token=` WS auth query. Any non-empty value is accepted. */
export function getAuthToken(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem("md_access_token") || "anonymous";
}
