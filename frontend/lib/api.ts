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
  static_ip_configured?: boolean;
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

export async function getAuthStatus(): Promise<AuthStatus> {
  const res = await apiFetch("/api/auth/status", { cache: "no-store" });
  return jsonOrThrow<AuthStatus>(res);
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

export async function getCaptureStatus(): Promise<CaptureStatus> {
  const res = await apiFetch("/api/capture/status", { cache: "no-store" });
  return jsonOrThrow<CaptureStatus>(res);
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

export async function getCaptureHistory(): Promise<CaptureHistory> {
  const res = await apiFetch("/api/capture/history", { cache: "no-store" });
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

export interface DashboardStats {
  generated_at: number;
  capture_running: boolean;
  trading_date: string | null;
  expected_frames_per_session: number;
  monitor: MonitorPayload | null;
  monitor_persisted: boolean;
  compression: CompressionProgressPayload | null;
  compression_history: CompressionHistory;
}

export async function getStats(): Promise<DashboardStats> {
  const res = await apiFetch("/api/stats", { cache: "no-store" });
  return jsonOrThrow<DashboardStats>(res);
}

export async function getStockDepth(symbol: string): Promise<StockDepthSnapshot> {
  const res = await apiFetch(
    `/api/capture/stocks/${encodeURIComponent(symbol)}/depth`,
    { cache: "no-store" },
  );
  return normalizeStockDepth(await jsonOrThrow<unknown>(res));
}
