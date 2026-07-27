/** Tagged-envelope protocol types (mirror backend app/ws/protocol.py). */

export interface WsEnvelope {
  type: string;
  payload?: unknown;
}

export const MSG = {
  MARKET_HEADER: "MarketHeader",
  OPTION_GRID: "OptionGrid",
  OPTION_GRID_DELTA: "OptionGridDelta",
  STOCK_BOARD: "StockBoard",
  CAPTURE_STATUS: "CaptureStatus",
  HEARTBEAT: "Heartbeat",
  SESSION_STATUS: "SessionStatus",
  LOG: "Log",
  HISTORICAL_JOB_UPDATE: "HistoricalJobUpdate",
  COMPRESSION_PROGRESS: "CompressionProgress",
} as const;

export interface MarketHeaderPayload {
  underlying: string;
  expiry: string;
  spot: number;
  atm: number;
  vix: number;
  risk_free_rate: number;
  timestamp: number;
  sequence: number;
}

/** Per-side option-chain columns (aligned to the strikes array). */
export interface GridBlock {
  oi: number[];
  change_in_oi: number[];
  volume: number[];
  iv: number[];
  delta: number[];
  gamma: number[];
  theta: number[];
  vega: number[];
  rho: number[];
  bid: number[];
  ask: number[];
  ltp: number[];
  change: number[];
}

export interface OptionGridPayload {
  underlying: string;
  expiry: string;
  strikes: number[];
  calls: GridBlock;
  puts: GridBlock;
  market_atm: number;
  max_pain: number;
  spot_atm: number;
  spot: number;
  vix: number;
}

export interface StockFutureRow {
  expiry: string;
  ltp: number;
  oi: number;
}

export interface DepthLevel {
  level: number;
  bid_price: number;
  bid_qty: number;
  bid_orders: number;
  ask_price: number;
  ask_qty: number;
  ask_orders: number;
}

export interface StockRow {
  tradingsymbol: string;
  name: string;
  spot_ltp: number;
  futures: StockFutureRow[];
  live_spread: number;
  daily_spread: number;
}

export interface StockDepthFuture {
  label: string;
  expiry: string;
  depth: DepthLevel[];
}

export interface StockDepthSnapshot {
  tradingsymbol: string;
  name: string;
  spot_depth: DepthLevel[];
  futures: StockDepthFuture[];
}

export interface StockBoardPayload {
  timestamp: number;
  stocks: StockRow[];
}

export interface PerUnderlyingStatus {
  underlying: string;
  connected: boolean;
  last_tick_ms: number | null;
  frames_written: number;
  frames_expected: number;
  frame_loss_pct: number;
  file_bytes: number;
  avg_bytes_per_frame: number;
  projected_eod_bytes: number;
  heartbeat_ok: boolean;
  heartbeat_age_ms: number | null;
  /** False when the live feed has frozen (duplicate/absent ticks). */
  data_fresh: boolean;
  unmatched: number;
  /** Ticks routed into this stream (separates one frozen underlying from a dead feed). */
  applied?: number;
  /** This stream's writer queue depth. */
  writer_pending?: number;
}

export interface GlobalStatus {
  tokens: number;
  fps: number;
  disk_bytes: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  captures: number;
  dropped_batches: number;
  drop_rate_pct: number;
  ingestion_degraded: boolean;
  uptime_ms: number;
  frames_written: number;
  frames_expected: number;
  frame_loss_pct: number;
  snapshot_ms: number;
  writer_lag_max: number;
  /** ms since the feed content last changed (null before the first tick). */
  data_age_ms: number | null;
  /** ms since the last batch of any kind arrived (null before the first tick). */
  liveness_age_ms: number | null;
  /** True when data has not changed within CAPTURE_STALE_SECONDS. */
  stale: boolean;
  /** True when stale or a self-driven reconnect is in progress. */
  degraded: boolean;
  /** Count of consecutive unchanged (frozen) batches observed. */
  frozen_batches: number;
  /** Number of self-driven ticker reconnects triggered this session. */
  reconnects: number;
  /** Active reconnect tier: 1 = reuse token, 2 = fresh token from calspread. */
  reconnect_tier?: number;
  /** Completed backoff cycles while the feed stayed stale. */
  reconnect_cycles?: number;
  /** True once reconnect recovery is exhausted (needs a process restart). */
  exhausted?: boolean;
  /** Access-token refreshes fetched during recovery. */
  token_refreshes?: number;
  last_token_refresh_ms?: number | null;
  token_age_ms?: number | null;
  // --- per-session data loss ---
  /** Times the 1 Hz grid fell so far behind it had to resync (lost whole seconds). */
  grid_gaps?: number;
  /** Total grid seconds that could never be written. */
  grid_seconds_lost?: number;
  /** Seconds captured while the feed was stale (duplicate values, no fresh data). */
  frozen_seconds?: number;
  /** Frames the grid should have produced over the ELAPSED capture span. */
  session_frames_expected?: number;
  /** Loss measured against elapsed time (not the full-day baseline). */
  session_loss_pct?: number;
  /** Ticks that matched no subscribed instrument. */
  unmatched_ticks?: number;
  batches_received?: number;
  ticks_received?: number;
  ticks_per_sec?: number;
  /** Hours of capture the remaining free disk can absorb. */
  disk_runway_hours?: number;
}

/** EOD zstd compression telemetry (CompressionProgress + persisted history). */
export interface CompressionProgressPayload {
  phase: string; // running | done | failed | idle
  files_done: number;
  files_total: number;
  bytes_done: number;
  bytes_total: number;
  zst_bytes: number;
  ratio: number;
  current_file: string | null;
  threads: number;
  started_at: number;
  updated_at: number;
  elapsed_ms: number;
  file_elapsed_ms: number;
  avg_file_ms: number;
  throughput_mbps: number;
}

export interface CaptureStatusPayload {
  per_underlying: PerUnderlyingStatus[];
  global: GlobalStatus;
}
