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
  unmatched: number;
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

export interface SessionStatusPayload {
  phase: string;
  diagnostics?: Record<string, unknown>;
}
