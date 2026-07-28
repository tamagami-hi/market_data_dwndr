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
  GlobalStatus,
  PerUnderlyingStatus,
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
    reconnect_tier: finite(global.reconnect_tier),
    reconnect_cycles: finite(global.reconnect_cycles),
    exhausted: bool(global.exhausted),
    token_refreshes: finite(global.token_refreshes),
    last_token_refresh_ms: optionalFinite(global.last_token_refresh_ms),
    token_age_ms: optionalFinite(global.token_age_ms),
    grid_gaps: finite(global.grid_gaps),
    grid_seconds_lost: finite(global.grid_seconds_lost),
    frozen_seconds: finite(global.frozen_seconds),
    session_frames_expected: finite(global.session_frames_expected),
    session_loss_pct: finite(global.session_loss_pct),
    unmatched_ticks: finite(global.unmatched_ticks),
    batches_received: finite(global.batches_received),
    ticks_received: finite(global.ticks_received),
    ticks_per_sec: finite(global.ticks_per_sec),
    disk_runway_hours: finite(global.disk_runway_hours),
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
    grid_gaps: finite(session.grid_gaps),
    grid_seconds_lost: finite(session.grid_seconds_lost),
    frozen_seconds: finite(session.frozen_seconds),
    dropped_batches: finite(session.dropped_batches),
    drop_rate_pct: finite(session.drop_rate_pct),
    unmatched_ticks: finite(session.unmatched_ticks),
    ticks_received: finite(session.ticks_received),
    reconnects: finite(session.reconnects),
    token_refreshes: finite(session.token_refreshes),
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
