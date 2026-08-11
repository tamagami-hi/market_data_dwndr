/** Tagged-envelope protocol types (mirror backend app/ws/protocol.py). */

export interface WsEnvelope {
  type: string;
  payload?: unknown;
  /** Transport diagnostics, not market data (see backend app/ws/protocol.py envelope). */
  meta?: {
    /**
     * Server-side build latency in ms: measured from immediately before the first
     * Greeks reconstruction until the whole 1 Hz batch is encoded and ready for the
     * websocket hub. Server-measured on purpose — comparing a server timestamp with the
     * browser clock would report clock skew, not latency.
     */
    pipeline_ms?: number;
    /** Portion of pipeline_ms spent reconstructing IV/Greeks for every chain. */
    greeks_ms?: number;
    /** Portion spent building the columnar stock board (all legs, L1–L5). */
    stocks_ms?: number;
  };
}

export const MSG = {
  MARKET_HEADER: "MarketHeader",
  OPTION_GRID: "OptionGrid",
  OPTION_GRID_DELTA: "OptionGridDelta",
  STOCK_BOARD: "StockBoard",
  INDEX_FNO_BOARD: "IndexFnoBoard",
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

export interface DepthLevel {
  level: number;
  bid_price: number;
  bid_qty: number;
  bid_orders: number;
  ask_price: number;
  ask_qty: number;
  ask_orders: number;
}

/** One depth level, columnar: each array is indexed by stock row. */
export interface DepthLevelColumns {
  bid_price: number[];
  bid_qty: number[];
  bid_orders: number[];
  ask_price: number[];
  ask_qty: number[];
  ask_orders: number[];
}

/** Every captured scalar for one leg, columnar (indexed by stock row). */
export interface LegScalarColumns {
  ltp: number[];
  oi: number[];
  volume: number[];
  buy_quantity: number[];
  sell_quantity: number[];
  oi_day_high: number[];
  oi_day_low: number[];
  ohlc_open: number[];
  ohlc_high: number[];
  ohlc_low: number[];
  ohlc_close: number[];
}

export interface LegColumns {
  scalars: LegScalarColumns;
  /** Exactly 5 entries: index 0 = L1 (best) … index 4 = L5. */
  depth: DepthLevelColumns[];
}

export type StockLegName = "spot" | "fut_current" | "fut_mid" | "fut_far";

/**
 * Full stock board, streamed every second.
 *
 * Columnar: one array per field rather than an object per stock, which is what makes
 * shipping all 4 legs x (11 scalars + 5 depth levels x 6 fields) affordable. Read a
 * stock by its row index — `names[i]`, `legs.spot.scalars.ltp[i]`, etc.
 */
export interface StockBoardPayload {
  timestamp: number;
  count: number;
  tradingsymbols: string[];
  names: string[];
  future_expiries: string[][];
  legs: Record<StockLegName, LegColumns>;
  live_spread: number[];
  daily_spread: number[];
}

/** A single stock projected out of the columnar board for rendering. */
export interface StockRow {
  row: number;
  tradingsymbol: string;
  name: string;
  spot_ltp: number;
  futures: StockFutureRow[];
  live_spread: number;
  daily_spread: number;
}

export interface StockFutureRow {
  expiry: string;
  ltp: number;
  oi: number;
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

/**
 * The index-F&O board: each configured index's spot plus its nearest futures.
 *
 * Identical wire shape to `StockBoardPayload` — same four legs, same columnar encoding —
 * so both boards render through one code path. Only the identity arrays differ: an index
 * row is named by `underlyings[i]` (NIFTY, BANKNIFTY, …) and `spot_symbols[i]`, because
 * the on-disk index record carries no tradingsymbol.
 */
export interface IndexFnoBoardPayload {
  timestamp: number;
  count: number;
  underlyings: string[];
  spot_symbols: string[];
  future_expiries: string[][];
  legs: Record<StockLegName, LegColumns>;
  live_spread: number[];
  daily_spread: number[];
}

/**
 * One index projected out of the columnar board.
 *
 * Structurally a `StockRow` so the shared row renderers accept it unchanged:
 * `tradingsymbol` carries the underlying and `name` the spot symbol.
 */
export type IndexFnoRow = StockRow;

export interface PerUnderlyingStatus {
  underlying: string;
  connected: boolean;
  last_tick_ms: number | null;
  frames_written: number;
  frames_expected: number;
  /** Loss vs the WHOLE-DAY baseline — a completeness figure, low early in the session. */
  frame_loss_pct: number;
  /** Frames the grid should have produced over the elapsed span (health baseline). */
  session_frames_expected?: number;
  /** Loss vs writable grid seconds — gaps and write failures only (above ~0 = real loss). */
  session_loss_pct?: number;
  /** Every grid second the loop reached, stale ones included. */
  grid_seconds_elapsed?: number;
  /** Total loss vs elapsed grid seconds, stale-suppressed seconds included. */
  data_loss_pct?: number;
  /** Share of the full session captured so far. */
  day_complete_pct?: number;
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
  // --- per-artifact lifecycle (each artifact answers for itself) ---
  /** This artifact's market-session phase: INACTIVE/BOOTSTRAP/PRE_OPEN/OPEN/CLOSED. */
  market_phase?: MarketPhase | null;
  /** True when a frame is owed for this artifact right now. */
  capture_active?: boolean | null;
  /** Age of this artifact's last relevant update; null when it has never updated. */
  artifact_age_ms?: number | null;
  /** True when this artifact is not updating while the transport is alive. */
  artifact_stale?: boolean;
  /** Timestamp of this artifact's last persisted frame. */
  last_frame_ms?: number | null;
}

/** Market-session lifecycle phase. Independent of feed health. */
export type MarketPhase = "INACTIVE" | "BOOTSTRAP" | "PRE_OPEN" | "OPEN" | "CLOSED";

/**
 * Feed health, kept strictly separate from the market phase: PRE_OPEN + HEALTHY and
 * OPEN + TRANSPORT_STALE are both meaningful states.
 */
export type FeedHealth =
  | "INACTIVE"
  | "HEALTHY"
  | "QUIET"
  | "ARTIFACT_STALE"
  | "TRANSPORT_STALE"
  | "RECOVERY_PENDING"
  | "RECOVERY_ABANDONED";

/** Transport + subscription-capacity telemetry (distinguishes a dead socket from a quiet market). */
export interface TransportStatus {
  connected: boolean;
  queue_depth: number;
  batches_received: number;
  dropped_batches: number;
  subscribed_tokens?: number;
  subscription_limit?: number;
  subscription_safe_limit?: number;
  subscription_remaining?: number;
  subscription_utilisation_pct?: number;
  subscription_shards?: number;
  subscription_over_threshold?: boolean;
  subscription_exceeds_capacity?: boolean;
  subscription_breakdown?: Record<string, number>;
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
  /** Deepest writer queue depth. Kept for /api/status consumers and logs; no longer on
   *  the dashboard, where a queue depth was noise next to the loss metrics. */
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
  /** Seconds the CURRENT continuous stale spell has run (0 when ticks are fresh). */
  stale_spell_seconds?: number;
  /** Longest stale spell this session, carried across process restarts. */
  longest_stale_spell_seconds?: number;
  /** Times this trading date has restarted the process over a dead feed. */
  escalations?: number;
  /** True when the day's restart budget is spent: capture is up but has no data. */
  recovery_abandoned?: boolean;
  /** True only while the exchange is trading — staleness is a fault worth restarting for. */
  recovery_armed?: boolean;
  /** True once recovery has been abandoned for the day (needs operator attention). */
  exhausted?: boolean;
  /** Access-token refreshes fetched during recovery. */
  token_refreshes?: number;
  last_token_refresh_ms?: number | null;
  token_age_ms?: number | null;
  // --- three-signal feed health (§11/§12), separate from the market phase (§23) ---
  /** Classified feed health; worst-first precedence. */
  feed_health?: FeedHealth | null;
  /** Milliseconds since ANY broker packet arrived (the transport signal). */
  transport_age_ms?: number | null;
  /** Per-artifact last-relevant-update ages; null means never updated. */
  artifact_ages_ms?: Record<string, number | null>;
  /** Artifacts not updating while the transport is alive. */
  stale_artifacts?: string[];
  /** The session phase of the reference artifact. Independent of feed health. */
  market_phase?: MarketPhase | null;
  /** Whether a frame is owed right now. */
  capture_expected?: boolean | null;
  /** Transport + subscription capacity. */
  transport?: TransportStatus;
  // --- session-scheduled completeness (uptime-independent, §17) ---
  /** Seconds this session is scheduled to capture over the whole trading day. */
  scheduled_seconds?: number;
  /** Scheduled seconds owed so far today — counts time the process was NOT running. */
  scheduled_seconds_elapsed?: number;
  /** Seconds actually captured. */
  captured_seconds?: number;
  /** Loss against the scheduled grid: the honest completeness figure. */
  scheduled_loss_pct?: number;
  /** Grid seconds reached while nothing was scheduled. Never counted as loss. */
  unscheduled_seconds?: number;
  /** Total missing scheduled seconds, whatever the cause. */
  missing_seconds?: number;
  /** Missing because the feed was stale and writes were suppressed. */
  stale_feed_seconds?: number;
  /** Missing because the application or server was not running. */
  downtime_seconds?: number;
  /** Missing because persistence failed. */
  write_path_seconds?: number;
  /** Missing with no determinable cause — visible rather than forced into a category. */
  unclassified_seconds?: number;
  // --- per-session data loss ---
  /** Times the 1 Hz grid fell so far behind it had to resync (lost whole seconds). */
  grid_gaps?: number;
  /** Total grid seconds that could never be written. */
  grid_seconds_lost?: number;
  /** Grid seconds NOT written because the feed was stale (frozen or absent values). */
  stale_seconds?: number;
  /** Distinct stale spells — one long freeze vs many brief blips. */
  stale_events?: number;
  /** True when the engine is configured to skip writing stale frames. */
  stale_writes_suppressed?: boolean;
  /** Every grid second the loop reached, whether or not a frame was persisted. */
  grid_seconds_elapsed?: number;
  /** Frames the grid should have produced over the ELAPSED capture span. */
  session_frames_expected?: number;
  /** Loss vs writable seconds (elapsed minus stale): gaps and write failures only. */
  session_loss_pct?: number;
  /** Total market-data loss vs every elapsed grid second, stale seconds included. */
  data_loss_pct?: number;
  /** Ticks that matched no subscribed instrument. */
  unmatched_ticks?: number;
  batches_received?: number;
  ticks_received?: number;
  ticks_per_sec?: number;
  /** First grid second of the session (survives a restart via the persisted snapshot). */
  first_grid_ms?: number | null;
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
