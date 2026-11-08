import { expect, test } from "vitest";

import { normalizeSessionReport } from "@/lib/monitor/sessionReport";
import { normalizeSessionPage, normalizeSessionSummary } from "@/lib/monitor/sessionSummary";

test("an undescribed row gains no report fields", () => {
  expect(normalizeSessionReport({ trading_date: "2026-07-01" })).toEqual({});
});

test("null ledger counts stay absent instead of becoming zero", () => {
  const report = normalizeSessionReport({
    incidents_recorded: false,
    process_starts: null,
    failed_starts: null,
    completeness_pct: null,
    outages: [],
  });
  expect(report).toEqual({ incidents_recorded: false, outages: [] });
});

test("malformed parts are dropped one by one, never the whole row", () => {
  const report = normalizeSessionReport({
    outcome: "excellent",
    outcome_basis: "vibes",
    completeness_pct: 99.9,
    session_window: { open: 9 },
    archive_reconciliation: { worst_artifact: "NIFTY" },
    eod: { status: "ok" },
    loss_causes: { restart_loop_seconds: -5, stale_feed_seconds: "x", maintenance_seconds: 30 },
    outages: [
      { cause: "restart", start_at: 1, end_at: 2, seconds: 1 },
      { cause: "restart", start_at: "a", end_at: 2 },
      "junk",
    ],
    datasets_missing: [{ dataset: "NIFTY", seconds: 10 }, { seconds: 10 }],
    stops: [{ at: 5, reason: "market_close", clean: "yes", count: 0 }, { reason: "x" }],
    explanation: ["one", 2, "three"],
  });
  expect(report.outcome).toBeUndefined();
  expect(report.outcome_basis).toBeUndefined();
  expect(report.completeness_pct).toBe(99.9);
  expect(report.session_window).toBeUndefined();
  expect(report.archive_reconciliation).toBeUndefined();
  expect(report.eod).toBeUndefined();
  expect(report.loss_causes).toMatchObject({
    restart_loop_seconds: 0,
    stale_feed_seconds: 0,
    maintenance_seconds: 30,
  });
  expect(report.outages).toEqual([
    {
      cause: "restart",
      start_at: 1,
      end_at: 2,
      recovered: true,
      seconds: 1,
      trigger: null,
      failed_starts: 0,
      exceptions: [],
      skipped_indices: [],
    },
  ]);
  expect(report.datasets_missing?.map((d) => d.dataset)).toEqual(["NIFTY"]);
  expect(report.stops).toEqual([{ at: 5, reason: "market_close", clean: null, count: 1 }]);
  expect(report.explanation).toEqual(["one", "three"]);
});

test("a session page keeps good rows, drops bad ones and reads its cursor", () => {
  const page = normalizeSessionPage({
    sessions: [{ trading_date: "2026-10-01", streams: [] }, { trading_date: 5 }],
    total: 74,
    next_before: "2026-10-01",
  });
  expect(page?.sessions.map((s) => s.trading_date)).toEqual(["2026-10-01"]);
  expect(page?.total).toBe(74);
  expect(page?.next_before).toBe("2026-10-01");
  expect(normalizeSessionPage({ sessions: [] })).toEqual({ sessions: [], total: 0, next_before: null });
  expect(normalizeSessionPage({ total: 1 })).toBeNull();
  expect(normalizeSessionPage(null)).toBeNull();
});

test("a described row keeps the special-session window", () => {
  const session = normalizeSessionSummary({
    trading_date: "2026-11-08",
    streams: [],
    session_window: {
      open: "18:00",
      close: "19:15",
      is_special: true,
      note: null,
      trading_day: true,
      scheduled_seconds: 4_500,
      windows: [[1, 2], [3], "x"],
    },
  });
  expect(session?.session_window).toEqual({
    open: "18:00",
    close: "19:15",
    is_special: true,
    note: null,
    trading_day: true,
    scheduled_seconds: 4_500,
    windows: [[1, 2]],
  });
});
