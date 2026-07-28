import { afterEach, expect, test, vi } from "vitest";

import {
  ApiError,
  getAuthStatus,
  getCaptureHistory,
  getStats,
  getStockDepth,
  normalizeAuthStatus,
} from "@/lib/api";

const validDepth = () => Array.from({ length: 5 }, (_, index) => ({
  level: index + 1,
  bid_price: 100 + index,
  bid_qty: 10,
  bid_orders: 1,
  ask_price: 101 + index,
  ask_qty: 20,
  ask_orders: 2,
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

test("fetches and validates every frontend REST adapter", async () => {
  const fetchMock = vi.fn(async (input: string | URL) => {
    const url = String(input);
    if (url.endsWith("/api/auth/status")) {
      return new Response(JSON.stringify({
        configured: true,
        authenticated: true,
        trading_date: "2026-07-29",
        market_phase: "open",
        credentials_present: true,
        external_token_source_configured: true,
        risk_free_rate: 0.06,
        access_token_at: 1,
        risk_free_rate_as_of: "2026-07-29",
        capture_ready: true,
        capture: {
          available: true,
          running: true,
          trading_date: "2026-07-29",
          indices: ["NIFTY"],
          stocks: 200,
          tokens: 400,
          skipped_indices: [],
          error: null,
        },
        automation: {
          phase: "capture_window",
          last_action: "running",
          last_error: null,
          last_broker_poll_at: 1,
          eod_completed_date: null,
          eod_in_progress_date: null,
        },
      }), { status: 200 });
    }
    if (url.endsWith("/api/capture/history")) {
      return new Response(JSON.stringify({ available: false, totals: {}, sessions: [] }), { status: 200 });
    }
    if (url.endsWith("/api/stats")) {
      return new Response(JSON.stringify({ capture_running: false }), { status: 200 });
    }
    return new Response(JSON.stringify({
      tradingsymbol: "RELIANCE",
      name: "Reliance",
      spot_depth: validDepth(),
      futures: [{ label: "Current future", expiry: "2026-07-30", depth: validDepth() }],
    }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);

  const controller = new AbortController();
  expect(await getAuthStatus(controller.signal)).toMatchObject({ authenticated: true });
  expect(await getCaptureHistory(controller.signal)).toMatchObject({ available: false });
  expect(await getStats(controller.signal)).toMatchObject({ capture_running: false });
  expect((await getStockDepth("RELIANCE & CO")).spot_depth).toHaveLength(5);
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("RELIANCE%20%26%20CO"))).toBe(true);
});

test("rejects malformed auth and preserves safe API error details", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ configured: true }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Temporarily unavailable" }), {
      status: 503,
      statusText: "Unavailable",
    }))
    .mockResolvedValueOnce(new Response("not-json", { status: 500, statusText: "Failed" })));

  await expect(getAuthStatus()).rejects.toMatchObject({ status: 502 });
  await expect(getStats()).rejects.toEqual(new ApiError(503, "Temporarily unavailable"));
  await expect(getCaptureHistory()).rejects.toMatchObject({ status: 500, message: "500 Failed" });
});

test("normalizes optional auth fields without trusting nested payloads", () => {
  expect(normalizeAuthStatus([])).toBeNull();
  expect(normalizeAuthStatus({ configured: false, authenticated: false })).toMatchObject({
    configured: false,
    capture: undefined,
    automation: undefined,
  });
  expect(normalizeAuthStatus({
    configured: true,
    authenticated: false,
    capture: { available: "yes", running: false },
    automation: [],
    credentials_present: "yes",
    risk_free_rate: Number.NaN,
  })).toMatchObject({
    capture: undefined,
    automation: undefined,
    credentials_present: undefined,
    risk_free_rate: null,
  });
});
