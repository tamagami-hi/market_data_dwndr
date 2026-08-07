import { describe, expect, test } from "vitest";

import {
  deriveMonitorView,
  normalizeCaptureHistory,
  normalizeCaptureStatus,
  normalizeDashboardStats,
} from "@/lib/monitor/viewModel";

describe("monitor adapters and view model", () => {
  test("defaults optional fields from an older capture payload without crashing", () => {
    const payload = normalizeCaptureStatus({
      per_underlying: [
        {
          underlying: "NIFTY",
          connected: true,
          frames_written: 12,
          frames_expected: 20,
          frame_loss_pct: 40,
          file_bytes: 100,
          avg_bytes_per_frame: 8,
          projected_eod_bytes: 200,
          heartbeat_ok: true,
          heartbeat_age_ms: null,
          last_tick_ms: null,
          data_fresh: true,
          unmatched: 0,
        },
      ],
      global: {
        tokens: 1,
        fps: 1,
        disk_bytes: 100,
        disk_free_bytes: 900,
        disk_total_bytes: 1000,
        captures: 12,
        dropped_batches: 0,
        drop_rate_pct: 0,
        ingestion_degraded: false,
        uptime_ms: 12_000,
        frames_written: 12,
        frames_expected: 20,
        frame_loss_pct: 40,
        snapshot_ms: 3,
        writer_lag_max: 0,
        data_age_ms: 100,
        liveness_age_ms: 100,
        stale: false,
        degraded: false,
        frozen_batches: 0,
        reconnects: 0,
      },
    });

    expect(payload?.per_underlying[0]).toMatchObject({
      session_loss_pct: 0,
      day_complete_pct: 60,
      applied: 0,
      writer_pending: 0,
    });
  });

  test("rejects malformed required monitor identity fields visibly", () => {
    expect(normalizeCaptureStatus({ per_underlying: [{ underlying: 42 }], global: {} })).toBeNull();
  });

  test("reads pre-suppression session records that stored frozen_seconds", () => {
    // Sessions archived before stale writes were suppressed recorded the count of frames
    // written WHILE frozen under `frozen_seconds`. Same quantity of unusable seconds, so
    // it must keep rendering in the Stale s column instead of silently reading 0.
    const stats = normalizeDashboardStats({
      capture_running: false,
      monitor: null,
      session_history: [{ trading_date: "2026-07-29", frozen_seconds: 201 }],
    });
    expect(stats?.session_history?.[0]?.stale_seconds).toBe(201);
    // A record carrying the new key wins over the legacy one.
    const current = normalizeDashboardStats({
      capture_running: false,
      monitor: null,
      session_history: [{ trading_date: "2026-07-31", stale_seconds: 13_579, frozen_seconds: 0 }],
    });
    expect(current?.session_history?.[0]?.stale_seconds).toBe(13_579);
  });

  test("normalizes an older stats payload with safe empty history defaults", () => {
    const stats = normalizeDashboardStats({
      generated_at: 100,
      capture_running: false,
      trading_date: "2026-07-29",
      expected_frames_per_session: 23_400,
      monitor: null,
      monitor_persisted: false,
      compression: null,
      compression_history: {
        samples: 0,
        avg_ratio: 0,
        avg_total_elapsed_ms: 0,
        avg_file_ms: 0,
        avg_throughput_mbps: 0,
        last: null,
      },
    });

    expect(stats?.session_history).toEqual([]);
  });

  test("rejects malformed session and capture-history records at the REST boundary", () => {
    expect(
      normalizeDashboardStats({
        capture_running: false,
        monitor: null,
        session_history: [{ trading_date: 42 }],
      }),
    ).toBeNull();
    expect(normalizeCaptureHistory({ available: true, totals: {}, sessions: "invalid" })).toBeNull();
    expect(
      normalizeCaptureHistory({
        available: true,
        generated_at: 1,
        totals: { sessions: 1, total_bytes: 10, raw_bytes: 10, archived_bytes: 0, data_files: 1 },
        sessions: [{
          trading_date: "2026-07-29",
          is_current: true,
          total_bytes: 10,
          raw_bytes: 10,
          archived_bytes: 0,
          data_files: 1,
          raw_files: 1,
          archived_files: 0,
          index_files: 1,
          stock_files: 0,
          indices: ["NIFTY"],
        }],
      })?.sessions[0].trading_date,
    ).toBe("2026-07-29");
  });

  test("derives past-session retained state and freshness without mutating stats", () => {
    const stats = normalizeDashboardStats({
      generated_at: 100,
      capture_running: false,
      trading_date: "2026-07-29",
      monitor_trading_date: "2026-07-28",
      expected_frames_per_session: 23_400,
      monitor: null,
      monitor_persisted: true,
      session_history: [],
      compression: null,
      compression_history: {
        samples: 0,
        avg_ratio: 0,
        avg_total_elapsed_ms: 0,
        avg_file_ms: 0,
        avg_throughput_mbps: 0,
        last: null,
      },
    });
    const monitor = normalizeCaptureStatus({ per_underlying: [], global: { fps: 0 } });
    const retainedStats = stats && monitor ? { ...stats, monitor } : null;
    const before = structuredClone(retainedStats);

    expect(
      deriveMonitorView({
        stats: retainedStats,
        hasLiveTelemetry: false,
        lastSuccessAt: 1_000,
        now: 70_000,
        restError: null,
      }),
    ).toMatchObject({
      source: "persisted",
      isPastSession: true,
      isRestStale: true,
    });
    expect(retainedStats).toEqual(before);
  });
});
