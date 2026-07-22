"use client";

import { getBackendUrl } from "@/lib/config";

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
}

export interface LoginResult {
  authenticated: boolean;
  trading_date: string;
  risk_free_rate: number | null;
}

export interface LoginProgress {
  attempt_id: string;
  step: "awaiting_totp" | "awaiting_risk_free_rate";
  method: "shared_session" | "local_credentials";
  trading_date: string;
  expires_at: number;
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
  const res = await fetch(`${getBackendUrl()}/api/auth/status`, { cache: "no-store" });
  return jsonOrThrow<AuthStatus>(res);
}

export async function postLogin(body: {
  request_token: string;
  risk_free_rate: number;
}): Promise<LoginResult> {
  const res = await fetch(`${getBackendUrl()}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrThrow<LoginResult>(res);
}

export async function startAutomatedLogin(): Promise<LoginProgress | LoginResult> {
  const res = await fetch(`${getBackendUrl()}/api/auth/login/start`, { method: "POST" });
  return jsonOrThrow<LoginProgress | LoginResult>(res);
}

export async function submitLoginTotp(attemptId: string, totp: string): Promise<LoginProgress> {
  const res = await fetch(`${getBackendUrl()}/api/auth/login/${attemptId}/totp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ totp }),
  });
  return jsonOrThrow<LoginProgress>(res);
}

export async function completeAutomatedLogin(
  attemptId: string,
  riskFreeRate: number,
): Promise<LoginResult> {
  const res = await fetch(`${getBackendUrl()}/api/auth/login/${attemptId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ risk_free_rate: riskFreeRate }),
  });
  return jsonOrThrow<LoginResult>(res);
}

export async function cancelAutomatedLogin(attemptId: string): Promise<void> {
  const res = await fetch(`${getBackendUrl()}/api/auth/login/${attemptId}`, {
    method: "DELETE",
  });
  if (!res.ok) await jsonOrThrow(res);
}

export async function getLoginUrl(): Promise<string> {
  const res = await fetch(`${getBackendUrl()}/api/auth/login-url`, { cache: "no-store" });
  const body = await jsonOrThrow<{ login_url: string }>(res);
  return body.login_url;
}

// --- capture control ---------------------------------------------------------

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
  const res = await fetch(`${getBackendUrl()}/api/capture/status`, { cache: "no-store" });
  return jsonOrThrow<CaptureStatus>(res);
}

export async function startCapture(): Promise<CaptureStatus> {
  const res = await fetch(`${getBackendUrl()}/api/capture/start`, { method: "POST" });
  return jsonOrThrow<CaptureStatus>(res);
}

export async function stopCapture(): Promise<CaptureStatus> {
  const res = await fetch(`${getBackendUrl()}/api/capture/stop`, { method: "POST" });
  return jsonOrThrow<CaptureStatus>(res);
}
