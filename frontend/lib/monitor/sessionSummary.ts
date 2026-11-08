import type { SessionPage, SessionStreamSummary, SessionSummary } from "@/lib/api";
import { asRecord, bool, finite, optionalFinite, pickFinite } from "@/lib/monitor/parse";
import { normalizeSessionReport } from "@/lib/monitor/sessionReport";
import type { GlobalStatus, PerUnderlyingStatus } from "@/lib/wsTypes";

/**
 * Schedule-relative accounting fields. Kept optional on purpose: rows recorded before the
 * scheduled breakdown existed lack them, and a fabricated 0 would read as "no loss".
 */
const SCHEDULE_FIELDS = [
  "scheduled_seconds",
  "scheduled_seconds_elapsed",
  "captured_seconds",
  "scheduled_loss_pct",
  "missing_seconds",
  "stale_feed_seconds",
  "restart_downtime_seconds",
  "not_running_seconds",
  "awaiting_first_tick_seconds",
  "write_path_seconds",
  "unclassified_seconds",
  "downtime_seconds",
  "generations",
  "ws_connect_attempts",
  "in_process_reconnects",
] as const;

function normalizeSessionStream(value: unknown): SessionStreamSummary | null {
  const stream = asRecord(value);
  if (!stream || typeof stream.underlying !== "string" || stream.underlying.length === 0) {
    return null;
  }
  return {
    underlying: stream.underlying,
    frames_written: finite(stream.frames_written),
    frame_loss_pct: finite(stream.frame_loss_pct),
    file_bytes: finite(stream.file_bytes),
  };
}

export function normalizeSessionSummary(value: unknown): SessionSummary | null {
  const session = asRecord(value);
  if (!session || typeof session.trading_date !== "string" || session.trading_date.length === 0) {
    return null;
  }
  const rawStreams = session.streams === undefined ? [] : session.streams;
  if (!Array.isArray(rawStreams)) return null;
  const streams = rawStreams.map(normalizeSessionStream);
  if (streams.some((stream) => stream === null)) return null;
  return {
    trading_date: session.trading_date,
    recorded_at: finite(session.recorded_at),
    uptime_ms: finite(session.uptime_ms),
    captures: finite(session.captures),
    frames_written: finite(session.frames_written),
    frames_expected: finite(session.frames_expected),
    frame_loss_pct: finite(session.frame_loss_pct),
    session_frames_expected: finite(session.session_frames_expected),
    session_loss_pct: finite(session.session_loss_pct),
    grid_seconds_elapsed: finite(session.grid_seconds_elapsed),
    data_loss_pct: finite(session.data_loss_pct),
    grid_gaps: finite(session.grid_gaps),
    grid_seconds_lost: finite(session.grid_seconds_lost),
    // Sessions recorded before stale-write suppression stored this as ``frozen_seconds``
    // (frames that WERE written while frozen); keep rendering them rather than showing 0.
    stale_seconds: finite(session.stale_seconds ?? session.frozen_seconds),
    stale_events: finite(session.stale_events),
    dropped_batches: finite(session.dropped_batches),
    drop_rate_pct: finite(session.drop_rate_pct),
    unmatched_ticks: finite(session.unmatched_ticks),
    ticks_received: finite(session.ticks_received),
    reconnects: finite(session.reconnects),
    token_refreshes: finite(session.token_refreshes),
    longest_stale_spell_seconds: finite(session.longest_stale_spell_seconds),
    escalations: finite(session.escalations),
    recovery_abandoned: bool(session.recovery_abandoned),
    exhausted: bool(session.exhausted),
    disk_bytes: finite(session.disk_bytes),
    streams: streams as SessionStreamSummary[],
    ...pickFinite(session, SCHEDULE_FIELDS),
    // Outcome, completeness, loss causes and the incident timeline the backend adds.
    ...normalizeSessionReport(session),
  };
}

/** ``GET /api/stats/sessions``: malformed rows are dropped, never the whole page. */
export function normalizeSessionPage(value: unknown): SessionPage | null {
  const page = asRecord(value);
  if (!page || !Array.isArray(page.sessions)) return null;
  return {
    sessions: page.sessions.flatMap((row) => {
      const session = normalizeSessionSummary(row);
      return session ? [session] : [];
    }),
    total: optionalFinite(page.total) ?? page.sessions.length,
    next_before: typeof page.next_before === "string" ? page.next_before : null,
  };
}

/**
 * Build a session-history row for the IN-PROGRESS capture from the live telemetry
 * stream. The recorded history (session-history.jsonl) only gains a session after
 * finalization, so without this the panel's Frames column never reflects the frames
 * currently being written to the .bin files.
 */
export function liveSessionSummary(
  tradingDate: string,
  globals: GlobalStatus,
  rows: PerUnderlyingStatus[],
): SessionSummary {
  return {
    trading_date: tradingDate,
    recorded_at: Date.now(),
    uptime_ms: globals.uptime_ms,
    captures: globals.captures,
    frames_written: globals.frames_written,
    frames_expected: globals.frames_expected,
    frame_loss_pct: globals.frame_loss_pct,
    session_frames_expected: globals.session_frames_expected ?? 0,
    session_loss_pct: globals.session_loss_pct ?? 0,
    grid_seconds_elapsed: globals.grid_seconds_elapsed ?? 0,
    data_loss_pct: globals.data_loss_pct ?? 0,
    grid_gaps: globals.grid_gaps ?? 0,
    grid_seconds_lost: globals.grid_seconds_lost ?? 0,
    stale_seconds: globals.stale_seconds ?? 0,
    stale_events: globals.stale_events ?? 0,
    dropped_batches: globals.dropped_batches,
    drop_rate_pct: globals.drop_rate_pct,
    unmatched_ticks: globals.unmatched_ticks ?? 0,
    ticks_received: globals.ticks_received ?? 0,
    reconnects: globals.reconnects,
    token_refreshes: globals.token_refreshes ?? 0,
    longest_stale_spell_seconds: globals.longest_stale_spell_seconds ?? 0,
    escalations: globals.escalations ?? 0,
    recovery_abandoned: globals.recovery_abandoned ?? false,
    exhausted: globals.exhausted ?? false,
    disk_bytes: globals.disk_bytes,
    streams: rows.map((row) => ({
      underlying: row.underlying,
      frames_written: row.frames_written,
      frame_loss_pct: row.frame_loss_pct,
      file_bytes: row.file_bytes,
    })),
    // The live row carries the same scheduled accounting as a finalized record, so the
    // Missing column and cause bar mean the same thing for today as for history.
    ...pickFinite(globals as unknown as Record<string, unknown>, SCHEDULE_FIELDS),
  };
}
