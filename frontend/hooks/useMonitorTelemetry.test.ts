import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getStats: vi.fn(),
  getCaptureHistory: vi.fn(),
  envelopeHandlers: [] as Array<(envelope: { type: string; payload?: unknown }) => void>,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    getStats: mocks.getStats,
    getCaptureHistory: mocks.getCaptureHistory,
  };
});

vi.mock("@/lib/useTopic", () => ({
  useTopicEnvelopes: (_connection: unknown, handler: (envelope: { type: string; payload?: unknown }) => void) => {
    mocks.envelopeHandlers.push(handler);
  },
}));

vi.mock("@/lib/wsTopicConnection", () => ({
  captureStatusConnection: { id: "capture" },
  sessionConnection: { id: "session" },
}));

import { useMonitorTelemetry } from "@/hooks/useMonitorTelemetry";

const compressionHistory = {
  samples: 0,
  avg_ratio: 0,
  avg_total_elapsed_ms: 0,
  avg_file_ms: 0,
  avg_throughput_mbps: 0,
  last: null,
};

const capturePayload = (global: Record<string, unknown>) => ({
  per_underlying: [],
  global: {
    fps: 1,
    stale: false,
    degraded: false,
    reconnects: 0,
    data_age_ms: 0,
    ...global,
  },
});

beforeEach(() => {
  mocks.envelopeHandlers.length = 0;
  mocks.getStats.mockReset();
  mocks.getCaptureHistory.mockReset();
  mocks.getStats.mockResolvedValue({
    generated_at: 1,
    capture_running: false,
    trading_date: "2026-07-29",
    monitor_persisted: false,
    expected_frames_per_session: 23_400,
    monitor: null,
    compression: null,
    compression_history: compressionHistory,
  });
  mocks.getCaptureHistory.mockResolvedValue({
    available: true,
    generated_at: 1,
    totals: { sessions: 0, total_bytes: 0, raw_bytes: 0, archived_bytes: 0, data_files: 0 },
    sessions: [],
  });
});

test("polls immediately and records a successful refresh", async () => {
  const { result, unmount } = renderHook(() => useMonitorTelemetry());
  await waitFor(() => expect(mocks.getStats).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(result.current.freshness.lastSuccessAt).not.toBeNull());
  expect(result.current.context.tradingDate).toBe("2026-07-29");
  expect(result.current.history.capture?.available).toBe(true);
  unmount();
  expect(mocks.getStats.mock.calls[0][0].aborted).toBe(false);
});

test("accepts live capture events, bounded logs, and malformed-data recovery", async () => {
  const { result } = renderHook(() => useMonitorTelemetry());
  await waitFor(() => expect(mocks.envelopeHandlers).toHaveLength(2));
  const capture = mocks.envelopeHandlers[0];

  act(() => capture({
    type: "CaptureStatus",
    payload: {
      per_underlying: [],
      global: { fps: 1, stale: true, degraded: true, reconnects: 1, data_age_ms: 2_000 },
    },
  }));
  expect(result.current.live.globals?.stale).toBe(true);
  expect(result.current.live.logs[0].kind).toBe("alert");

  act(() => capture({ type: "CaptureStatus", payload: { per_underlying: "invalid" } }));
  expect(result.current.freshness.payloadError).toMatch(/invalid shape/);

  act(() => capture({
    type: "CompressionProgress",
    payload: { phase: "running", files_done: 1, files_total: 2 },
  }));
  expect(result.current.live.compression?.phase).toBe("running");
});

test("rejects malformed history and replaces REST compression snapshots", async () => {
  mocks.getStats.mockResolvedValue({
    generated_at: 1,
    capture_running: false,
    monitor_persisted: false,
    monitor: null,
    compression: { phase: "first" },
    compression_history: compressionHistory,
  });
  mocks.getCaptureHistory.mockResolvedValue(null);
  const { result, unmount } = renderHook(() => useMonitorTelemetry());

  await waitFor(() => expect(result.current.live.compression?.phase).toBe("first"));
  await waitFor(() => expect(result.current.freshness.restError).toMatch(/history response was malformed/i));
  unmount();
});

test("handles feed recovery, reconnect context, and session log variants", async () => {
  const { result, unmount } = renderHook(() => useMonitorTelemetry());
  await waitFor(() => expect(mocks.envelopeHandlers).toHaveLength(2));
  const [capture, session] = mocks.envelopeHandlers;

  act(() => capture({ type: "CompressionProgress", payload: "invalid" }));
  expect(result.current.freshness.payloadError).toMatch(/compression update/);
  act(() => capture({ type: "Unrelated", payload: {} }));
  act(() => capture({
    type: "CaptureStatus",
    payload: capturePayload({ stale: true, degraded: true, reconnects: 1, data_age_ms: null }),
  }));
  act(() => capture({
    type: "CaptureStatus",
    payload: capturePayload({ stale: false, degraded: false, reconnects: 2, reconnect_tier: 2 }),
  }));
  act(() => session({ type: "Log", payload: { message: "writer online" } }));
  act(() => session({ type: "Log", payload: { message: 42 } }));
  act(() => session({ type: "SessionStatus", payload: { phase: "capture" } }));
  act(() => session({ type: "SessionStatus", payload: null }));

  expect(result.current.live.logs.some((line) => line.text.includes("recovered"))).toBe(true);
  expect(result.current.live.logs.some((line) => line.text.includes("fresh token"))).toBe(true);
  expect(result.current.live.logs.some((line) => line.text === "writer online")).toBe(true);
  expect(result.current.live.logs.some((line) => line.text === "Session: unknown")).toBe(true);
  unmount();
});

test("keeps retained REST data distinct from a new live capture", async () => {
  mocks.getStats.mockResolvedValue({
    generated_at: 1,
    capture_running: false,
    trading_date: "2026-07-29",
    monitor_persisted: true,
    monitor_trading_date: "2026-07-29",
    monitor: capturePayload({ fps: 2 }).global
      ? capturePayload({ fps: 2 })
      : null,
    compression: null,
    compression_history: compressionHistory,
  });
  const { result, unmount } = renderHook(() => useMonitorTelemetry());
  await waitFor(() => expect(result.current.source.type).toBe("persisted"));

  mocks.getStats.mockResolvedValue({
    generated_at: 2,
    capture_running: true,
    trading_date: "2026-07-29",
    monitor_persisted: true,
    monitor_trading_date: "2026-07-29",
    monitor: capturePayload({ fps: 2 }),
    compression: null,
    compression_history: compressionHistory,
  });
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await waitFor(() => expect(result.current.context.captureRunning).toBe(true));
  expect(result.current.source.type).toBe("persisted");
  expect(result.current.live.globals).toBeNull();

  act(() => mocks.envelopeHandlers[0]({
    type: "CaptureStatus",
    payload: capturePayload({ fps: 5 }),
  }));
  expect(result.current.source.type).toBe("live");
  unmount();
});

test("surfaces REST rejection and recovers on the next visible refresh", async () => {
  mocks.getStats.mockRejectedValue(new Error("stats offline"));
  mocks.getCaptureHistory.mockRejectedValue("history offline");
  const { result, unmount } = renderHook(() => useMonitorTelemetry());
  await waitFor(() => expect(result.current.freshness.restError).toMatch(/stats offline/));
  expect(result.current.freshness.restError).toMatch(/Capture history refresh failed/);

  mocks.getStats.mockResolvedValue({
    generated_at: 3,
    capture_running: false,
    trading_date: "2026-07-29",
    monitor_persisted: false,
    monitor: null,
    compression: null,
    compression_history: compressionHistory,
  });
  mocks.getCaptureHistory.mockResolvedValue({ available: false });
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await waitFor(() => expect(result.current.context.tradingDate).toBe("2026-07-29"));
  expect(result.current.freshness.restError).toMatch(/history is unavailable/);
  unmount();
});
