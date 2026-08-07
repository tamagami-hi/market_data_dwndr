"use client";

import { getBackendUrl } from "@/lib/config";
import { normalizeStockDepth } from "@/lib/stockDepth";
import type { AutomationStateView } from "@/lib/automationStatus";
import type {
  CompressionProgressPayload,
  GlobalStatus,
  PerUnderlyingStatus,
  StockDepthSnapshot,
} from "@/lib/wsTypes";

export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
  trading_date?: string;
  market_phase?: string;
  credentials_present?: boolean;
  external_token_source_configured?: boolean;
  risk_free_rate?: number | null;
  access_token_at?: number | null;
  risk_free_rate_as_of?: string | null;
  capture_ready?: boolean;
  capture?: CaptureStatus;
  automation?: AutomationStateView;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : null;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function optionalBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArray(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? [...value]
    : undefined;
}

function normalizeCaptureStatus(value: unknown): CaptureStatus | undefined {
  const capture = asRecord(value);
  if (
    !capture ||
    typeof capture.available !== "boolean" ||
    typeof capture.running !== "boolean"
  ) {
    return undefined;
  }
  return {
    available: capture.available,
    running: capture.running,
    trading_date: nullableString(capture.trading_date),
    indices: stringArray(capture.indices),
    stocks: nullableNumber(capture.stocks) ?? undefined,
    tokens: nullableNumber(capture.tokens) ?? undefined,
    skipped_indices: stringArray(capture.skipped_indices),
    error: nullableString(capture.error),
  };
}

function normalizeAutomation(value: unknown): AutomationStateView | undefined {
  const automation = asRecord(value);
  if (!automation) return undefined;
  return {
    phase: optionalString(automation.phase),
    last_action: nullableString(automation.last_action),
    last_error: nullableString(automation.last_error),
    last_broker_poll_at: nullableNumber(automation.last_broker_poll_at),
    eod_completed_date: nullableString(automation.eod_completed_date),
    eod_in_progress_date: nullableString(automation.eod_in_progress_date),
  };
}

export function normalizeAuthStatus(value: unknown): AuthStatus | null {
  const status = asRecord(value);
  if (
    !status ||
    typeof status.configured !== "boolean" ||
    typeof status.authenticated !== "boolean"
  ) {
    return null;
  }
  return {
    configured: status.configured,
    authenticated: status.authenticated,
    trading_date: optionalString(status.trading_date),
    market_phase: optionalString(status.market_phase),
    credentials_present: optionalBoolean(status.credentials_present),
    external_token_source_configured: optionalBoolean(status.external_token_source_configured),
    risk_free_rate: nullableNumber(status.risk_free_rate),
    access_token_at: nullableNumber(status.access_token_at),
    risk_free_rate_as_of: nullableString(status.risk_free_rate_as_of),
    capture_ready: optionalBoolean(status.capture_ready),
    capture: normalizeCaptureStatus(status.capture),
    automation: normalizeAutomation(status.automation),
  };
}

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${getBackendUrl()}${path}`, init);
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export async function getAuthStatus(signal?: AbortSignal): Promise<AuthStatus> {
  const res = await apiFetch("/api/auth/status", { cache: "no-store", signal });
  const status = normalizeAuthStatus(await jsonOrThrow<unknown>(res));
  if (!status) throw new ApiError(502, "Auth status response was malformed.");
  return status;
}

// --- capture status ----------------------------------------------------------

export interface CaptureStatus {
  available: boolean;
  running: boolean;
  trading_date?: string | null;
  indices?: string[];
  stocks?: number;
  tokens?: number;
  skipped_indices?: string[];
  error?: string | null;
}

export interface CaptureHistorySession {
  trading_date: string;
  is_current: boolean;
  total_bytes: number;
  raw_bytes: number;
  archived_bytes: number;
  data_files: number;
  raw_files: number;
  archived_files: number;
  index_files: number;
  stock_files: number;
  indices: string[];
}

export interface CaptureHistory {
  available: boolean;
  generated_at: number | null;
  totals: {
    sessions: number;
    total_bytes: number;
    raw_bytes: number;
    archived_bytes: number;
    data_files: number;
  };
  sessions: CaptureHistorySession[];
}

export async function getCaptureHistory(signal?: AbortSignal): Promise<CaptureHistory> {
  const res = await apiFetch("/api/capture/history", { cache: "no-store", signal });
  return jsonOrThrow<CaptureHistory>(res);
}

// --- dashboard stats ---------------------------------------------------------

export interface CompressionRecord {
  trading_date: string;
  files: number;
  raw_bytes: number;
  zst_bytes: number;
  ratio: number;
  total_elapsed_ms: number;
  avg_file_ms: number;
  throughput_mbps: number;
  threads: number | null;
}

export interface CompressionHistory {
  samples: number;
  avg_ratio: number;
  avg_total_elapsed_ms: number;
  avg_file_ms: number;
  avg_throughput_mbps: number;
  last: CompressionRecord | null;
}

export interface MonitorPayload {
  per_underlying: PerUnderlyingStatus[];
  global: GlobalStatus;
}

export interface SessionStreamSummary {
  underlying: string;
  frames_written: number;
  frame_loss_pct: number;
  file_bytes: number;
}

/** One completed capture session's data-loss record (from session-history.jsonl). */
export interface SessionSummary {
  trading_date: string;
  recorded_at: number;
  uptime_ms: number;
  captures: number;
  frames_written: number;
  frames_expected: number;
  frame_loss_pct: number;
  session_frames_expected: number;
  session_loss_pct: number;
  /** Every grid second the session covered, stale ones included. */
  grid_seconds_elapsed: number;
  /** Total market-data loss, stale-suppressed seconds included. */
  data_loss_pct: number;
  grid_gaps: number;
  grid_seconds_lost: number;
  /** Grid seconds not written because the feed was stale. */
  stale_seconds: number;
  /** Distinct stale spells during the session. */
  stale_events: number;
  dropped_batches: number;
  drop_rate_pct: number;
  unmatched_ticks: number;
  ticks_received: number;
  reconnects: number;
  token_refreshes: number;
  /** Longest continuous stale spell this session, in seconds. */
  longest_stale_spell_seconds?: number;
  /** Times this session restarted the process over a dead feed. */
  escalations?: number;
  /** True when the day's restart budget was spent without restoring the feed. */
  recovery_abandoned?: boolean;
  exhausted: boolean;
  disk_bytes: number;
  streams: SessionStreamSummary[];
}

/** When the dashboard should actively refresh (capture running or pre-open auth window). */
export interface RefreshWindow {
  auth_poll_start: string;
  auth_poll_end: string;
  in_auth_window: boolean;
  should_refresh: boolean;
  local_time?: string;
}

export interface DashboardStats {
  generated_at: number;
  capture_running: boolean;
  trading_date: string | null;
  market_phase?: string | null;
  expected_frames_per_session: number;
  monitor: MonitorPayload | null;
  monitor_persisted: boolean;
  /** Trading date the monitor payload belongs to (may be an earlier session). */
  monitor_trading_date?: string | null;
  session_history?: SessionSummary[];
  refresh_window?: RefreshWindow;
  compression: CompressionProgressPayload | null;
  compression_history: CompressionHistory;
}

export async function getStats(signal?: AbortSignal): Promise<DashboardStats> {
  const res = await apiFetch("/api/stats", { cache: "no-store", signal });
  return jsonOrThrow<DashboardStats>(res);
}

export async function getStockDepth(symbol: string): Promise<StockDepthSnapshot> {
  const res = await apiFetch(
    `/api/capture/stocks/${encodeURIComponent(symbol)}/depth`,
    { cache: "no-store" },
  );
  return normalizeStockDepth(await jsonOrThrow<unknown>(res));
}
