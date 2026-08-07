import type {
  CaptureHistory,
  CaptureHistorySession,
  CompressionHistory,
  CompressionRecord,
  DashboardStats,
  MonitorPayload,
  SessionSummary,
  SessionStreamSummary,
} from "@/lib/api";
import type {
  CaptureStatusPayload,
  CompressionProgressPayload,
  FeedHealth,
  GlobalStatus,
  MarketPhase,
  PerUnderlyingStatus,
  TransportStatus,
} from "@/lib/wsTypes";

type UnknownRecord = Record<string, unknown>;

const EMPTY_COMPRESSION_HISTORY: CompressionHistory = {
  samples: 0,
  avg_ratio: 0,
  avg_total_elapsed_ms: 0,
  avg_file_ms: 0,
  avg_throughput_mbps: 0,
  last: null,
};

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function finite(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function optionalFinite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function normalizeUnderlying(value: unknown): PerUnderlyingStatus | null {
  const row = asRecord(value);
  if (!row || typeof row.underlying !== "string" || row.underlying.length === 0) return null;
  const frameLoss = finite(row.frame_loss_pct);
  return {
    underlying: row.underlying,
    connected: bool(row.connected),
    last_tick_ms: optionalFinite(row.last_tick_ms),
    frames_written: finite(row.frames_written),
    frames_expected: finite(row.frames_expected),
    frame_loss_pct: frameLoss,
    session_frames_expected: finite(row.session_frames_expected),
    session_loss_pct: finite(row.session_loss_pct),
    grid_seconds_elapsed: finite(row.grid_seconds_elapsed),
    data_loss_pct: finite(row.data_loss_pct),
    day_complete_pct: finite(row.day_complete_pct, Math.max(0, 100 - frameLoss)),
    file_bytes: finite(row.file_bytes),
    avg_bytes_per_frame: finite(row.avg_bytes_per_frame),
    projected_eod_bytes: finite(row.projected_eod_bytes),
    heartbeat_ok: bool(row.heartbeat_ok),
    heartbeat_age_ms: optionalFinite(row.heartbeat_age_ms),
    data_fresh: bool(row.data_fresh),
    unmatched: finite(row.unmatched),
    applied: finite(row.applied),
    writer_pending: finite(row.writer_pending),
    // Per-artifact lifecycle: each artifact reports its own phase and freshness, so a
    // frozen dataset is visible even when every other one is healthy.
    market_phase: phase(row.market_phase),
    capture_active: optionalBool(row.capture_active),
    artifact_age_ms: optionalFinite(row.artifact_age_ms),
    artifact_stale: bool(row.artifact_stale),
    last_frame_ms: optionalFinite(row.last_frame_ms),
  };
}

const MARKET_PHASES = new Set(["INACTIVE", "BOOTSTRAP", "PRE_OPEN", "OPEN", "CLOSED"]);
const FEED_HEALTHS = new Set([
  "INACTIVE",
  "HEALTHY",
  "QUIET",
  "ARTIFACT_STALE",
  "TRANSPORT_STALE",
  "RECOVERY_PENDING",
  "RECOVERY_ABANDONED",
]);

function phase(value: unknown): MarketPhase | null {
  return typeof value === "string" && MARKET_PHASES.has(value) ? (value as MarketPhase) : null;
}

function feedHealth(value: unknown): FeedHealth | null {
  return typeof value === "string" && FEED_HEALTHS.has(value) ? (value as FeedHealth) : null;
}

function optionalBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
}

function ageMap(value: unknown): Record<string, number | null> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, number | null> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    out[key] = optionalFinite(raw);
  }
  return out;
}

function normalizeTransport(value: unknown): TransportStatus | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return undefined;
  const t = value as Record<string, unknown>;
  const breakdown = t.subscription_breakdown;
  return {
    connected: bool(t.connected),
    queue_depth: finite(t.queue_depth),
    batches_received: finite(t.batches_received),
    dropped_batches: finite(t.dropped_batches),
    subscribed_tokens: finite(t.subscribed_tokens),
    subscription_limit: finite(t.subscription_limit),
    subscription_safe_limit: finite(t.subscription_safe_limit),
    subscription_remaining: finite(t.subscription_remaining),
    subscription_utilisation_pct: finite(t.subscription_utilisation_pct),
    subscription_shards: finite(t.subscription_shards, 1),
    subscription_over_threshold: bool(t.subscription_over_threshold),
    subscription_exceeds_capacity: bool(t.subscription_exceeds_capacity),
    subscription_breakdown:
      breakdown && typeof breakdown === "object" && !Array.isArray(breakdown)
        ? Object.fromEntries(
            Object.entries(breakdown as Record<string, unknown>).map(([k, v]) => [k, finite(v)]),
          )
        : {},
  };
}

function normalizeGlobal(value: unknown): GlobalStatus | null {
  const global = asRecord(value);
  if (!global || typeof global.fps !== "number" || !Number.isFinite(global.fps)) return null;
  return {
    tokens: finite(global.tokens),
    fps: finite(global.fps),
    disk_bytes: finite(global.disk_bytes),
    disk_free_bytes: finite(global.disk_free_bytes),
    disk_total_bytes: finite(global.disk_total_bytes),
    captures: finite(global.captures),
    dropped_batches: finite(global.dropped_batches),
    drop_rate_pct: finite(global.drop_rate_pct),
    ingestion_degraded: bool(global.ingestion_degraded),
    uptime_ms: finite(global.uptime_ms),
    frames_written: finite(global.frames_written),
    frames_expected: finite(global.frames_expected),
    frame_loss_pct: finite(global.frame_loss_pct),
    snapshot_ms: finite(global.snapshot_ms),
    writer_lag_max: finite(global.writer_lag_max),
    data_age_ms: optionalFinite(global.data_age_ms),
    liveness_age_ms: optionalFinite(global.liveness_age_ms),
    stale: bool(global.stale),
    degraded: bool(global.degraded),
    frozen_batches: finite(global.frozen_batches),
    reconnects: finite(global.reconnects),
    stale_spell_seconds: finite(global.stale_spell_seconds),
    longest_stale_spell_seconds: finite(global.longest_stale_spell_seconds),
    escalations: finite(global.escalations),
    recovery_abandoned: bool(global.recovery_abandoned),
    recovery_armed: bool(global.recovery_armed),
    exhausted: bool(global.exhausted),
    token_refreshes: finite(global.token_refreshes),
    // Three-signal feed health, kept separate from the market phase.
    feed_health: feedHealth(global.feed_health),
    transport_age_ms: optionalFinite(global.transport_age_ms),
    artifact_ages_ms: ageMap(global.artifact_ages_ms),
    stale_artifacts: stringList(global.stale_artifacts),
    market_phase: phase(global.market_phase),
    capture_expected: optionalBool(global.capture_expected),
    transport: normalizeTransport(global.transport),
    // Session-scheduled completeness: the denominator does not depend on uptime, so
    // downtime shows up in the loss figure instead of vanishing with the process.
    scheduled_seconds: finite(global.scheduled_seconds),
    scheduled_seconds_elapsed: finite(global.scheduled_seconds_elapsed),
    captured_seconds: finite(global.captured_seconds),
    scheduled_loss_pct: finite(global.scheduled_loss_pct),
    unscheduled_seconds: finite(global.unscheduled_seconds),
    missing_seconds: finite(global.missing_seconds),
    stale_feed_seconds: finite(global.stale_feed_seconds),
    downtime_seconds: finite(global.downtime_seconds),
    write_path_seconds: finite(global.write_path_seconds),
    unclassified_seconds: finite(global.unclassified_seconds),
    last_token_refresh_ms: optionalFinite(global.last_token_refresh_ms),
    token_age_ms: optionalFinite(global.token_age_ms),
    grid_gaps: finite(global.grid_gaps),
    grid_seconds_lost: finite(global.grid_seconds_lost),
    stale_seconds: finite(global.stale_seconds),
    stale_events: finite(global.stale_events),
    stale_writes_suppressed: bool(global.stale_writes_suppressed),
    grid_seconds_elapsed: finite(global.grid_seconds_elapsed),
    session_frames_expected: finite(global.session_frames_expected),
    session_loss_pct: finite(global.session_loss_pct),
    data_loss_pct: finite(global.data_loss_pct),
    unmatched_ticks: finite(global.unmatched_ticks),
    batches_received: finite(global.batches_received),
    ticks_received: finite(global.ticks_received),
    ticks_per_sec: finite(global.ticks_per_sec),
    first_grid_ms: optionalFinite(global.first_grid_ms),
  };
}

export function normalizeCaptureStatus(value: unknown): CaptureStatusPayload | null {
  const payload = asRecord(value);
  if (!payload || !Array.isArray(payload.per_underlying)) return null;
  const rows = payload.per_underlying.map(normalizeUnderlying);
  const global = normalizeGlobal(payload.global);
  if (!global || rows.some((row) => row === null)) return null;
  return { per_underlying: rows as PerUnderlyingStatus[], global };
}

function normalizeMonitor(value: unknown): MonitorPayload | null {
  return normalizeCaptureStatus(value);
}

export function normalizeCompressionProgress(value: unknown): CompressionProgressPayload | null {
  const item = asRecord(value);
  if (!item) return null;
  return {
    phase: text(item.phase, "idle"),
    files_done: finite(item.files_done),
    files_total: finite(item.files_total),
    bytes_done: finite(item.bytes_done),
    bytes_total: finite(item.bytes_total),
    zst_bytes: finite(item.zst_bytes),
    ratio: finite(item.ratio),
    current_file: optionalText(item.current_file),
    threads: finite(item.threads),
    started_at: finite(item.started_at),
    updated_at: finite(item.updated_at),
    elapsed_ms: finite(item.elapsed_ms),
    file_elapsed_ms: finite(item.file_elapsed_ms),
    avg_file_ms: finite(item.avg_file_ms),
    throughput_mbps: finite(item.throughput_mbps),
  };
}

function normalizeCompressionRecord(value: unknown): CompressionRecord | null {
  const item = asRecord(value);
  if (!item || typeof item.trading_date !== "string" || item.trading_date.length === 0) return null;
  return {
    trading_date: item.trading_date,
    files: finite(item.files),
    raw_bytes: finite(item.raw_bytes),
    zst_bytes: finite(item.zst_bytes),
    ratio: finite(item.ratio),
    total_elapsed_ms: finite(item.total_elapsed_ms),
    avg_file_ms: finite(item.avg_file_ms),
    throughput_mbps: finite(item.throughput_mbps),
    threads: optionalFinite(item.threads),
  };
}

function normalizeCompressionHistory(value: unknown): CompressionHistory {
  const item = asRecord(value);
  if (!item) return EMPTY_COMPRESSION_HISTORY;
  return {
    samples: finite(item.samples),
    avg_ratio: finite(item.avg_ratio),
    avg_total_elapsed_ms: finite(item.avg_total_elapsed_ms),
    avg_file_ms: finite(item.avg_file_ms),
    avg_throughput_mbps: finite(item.avg_throughput_mbps),
    last: normalizeCompressionRecord(item.last),
  };
}

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

function normalizeSessionSummary(value: unknown): SessionSummary | null {
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
  };
}

function normalizeCaptureHistorySession(value: unknown): CaptureHistorySession | null {
  const session = asRecord(value);
  if (
    !session ||
    typeof session.trading_date !== "string" ||
    session.trading_date.length === 0 ||
    typeof session.is_current !== "boolean" ||
    !Array.isArray(session.indices) ||
    !session.indices.every((item) => typeof item === "string")
  ) {
    return null;
  }
  return {
    trading_date: session.trading_date,
    is_current: session.is_current,
    total_bytes: finite(session.total_bytes),
    raw_bytes: finite(session.raw_bytes),
    archived_bytes: finite(session.archived_bytes),
    data_files: finite(session.data_files),
    raw_files: finite(session.raw_files),
    archived_files: finite(session.archived_files),
    index_files: finite(session.index_files),
    stock_files: finite(session.stock_files),
    indices: [...session.indices],
  };
}

export function normalizeCaptureHistory(value: unknown): CaptureHistory | null {
  const history = asRecord(value);
  if (!history || typeof history.available !== "boolean") return null;
  if (!history.available) {
    return {
      available: false,
      generated_at: optionalFinite(history.generated_at),
      totals: { sessions: 0, total_bytes: 0, raw_bytes: 0, archived_bytes: 0, data_files: 0 },
      sessions: [],
    };
  }
  const totals = asRecord(history.totals);
  if (!totals || !Array.isArray(history.sessions)) return null;
  const sessions = history.sessions.map(normalizeCaptureHistorySession);
  if (sessions.some((session) => session === null)) return null;
  return {
    available: true,
    generated_at: optionalFinite(history.generated_at),
    totals: {
      sessions: finite(totals.sessions),
      total_bytes: finite(totals.total_bytes),
      raw_bytes: finite(totals.raw_bytes),
      archived_bytes: finite(totals.archived_bytes),
      data_files: finite(totals.data_files),
    },
    sessions: sessions as CaptureHistorySession[],
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
  };
}

export function normalizeDashboardStats(value: unknown): DashboardStats | null {
  const stats = asRecord(value);
  if (!stats || typeof stats.capture_running !== "boolean") return null;
  const monitor = stats.monitor === null || stats.monitor === undefined
    ? null
    : normalizeMonitor(stats.monitor);
  if (stats.monitor && !monitor) return null;
  const rawSessionHistory = stats.session_history === undefined ? [] : stats.session_history;
  if (!Array.isArray(rawSessionHistory)) return null;
  const sessionHistory = rawSessionHistory.map(normalizeSessionSummary);
  if (sessionHistory.some((session) => session === null)) return null;
  const refresh = asRecord(stats.refresh_window);
  return {
    generated_at: finite(stats.generated_at),
    capture_running: stats.capture_running,
    trading_date: optionalText(stats.trading_date),
    market_phase: optionalText(stats.market_phase),
    expected_frames_per_session: finite(stats.expected_frames_per_session, 23_400),
    monitor,
    monitor_persisted: bool(stats.monitor_persisted),
    monitor_trading_date: optionalText(stats.monitor_trading_date),
    session_history: sessionHistory as SessionSummary[],
    refresh_window: refresh
      ? {
          auth_poll_start: text(refresh.auth_poll_start),
          auth_poll_end: text(refresh.auth_poll_end),
          in_auth_window: bool(refresh.in_auth_window),
          should_refresh: bool(refresh.should_refresh),
          local_time: text(refresh.local_time) || undefined,
        }
      : undefined,
    compression: normalizeCompressionProgress(stats.compression),
    compression_history: normalizeCompressionHistory(stats.compression_history),
  };
}

export interface MonitorView {
  source: "none" | "live" | "persisted";
  isPastSession: boolean;
  isRestStale: boolean;
  restError: string | null;
}

const REST_STALE_AFTER_MS = 60_000;

export function deriveMonitorView(input: {
  stats: DashboardStats | null;
  hasLiveTelemetry: boolean;
  lastSuccessAt: number | null;
  now: number;
  restError: string | null;
}): MonitorView {
  const hasPersisted = Boolean(input.stats?.monitor_persisted && input.stats.monitor);
  const source = input.hasLiveTelemetry
    ? "live"
    : hasPersisted
      ? "persisted"
      : "none";
  return {
    source,
    isPastSession: Boolean(
      hasPersisted &&
        input.stats?.monitor_trading_date &&
        input.stats.monitor_trading_date !== input.stats.trading_date,
    ),
    isRestStale:
      input.lastSuccessAt !== null &&
      input.now - input.lastSuccessAt >= REST_STALE_AFTER_MS,
    restError: input.restError,
  };
}
