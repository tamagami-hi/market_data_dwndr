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
} as const;

export interface MarketHeaderPayload {
  underlying: string;
  expiry: string;
  spot: number;
  atm: number;
  vix: number;
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

export interface StockRow {
  tradingsymbol: string;
  name: string;
  spot_ltp: number;
  futures: StockFutureRow[];
  live_spread: number;
  daily_spread: number;
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
  file_bytes: number;
  heartbeat_ok: boolean;
  unmatched: number;
}

export interface GlobalStatus {
  tokens: number;
  fps: number;
  disk_bytes: number;
  captures: number;
}

export interface CaptureStatusPayload {
  per_underlying: PerUnderlyingStatus[];
  global: GlobalStatus;
}

export interface SessionStatusPayload {
  phase: string;
  diagnostics?: Record<string, unknown>;
}
