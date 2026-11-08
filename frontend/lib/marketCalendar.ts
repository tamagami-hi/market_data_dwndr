"use client";

import { ApiError } from "@/lib/api";
import { getBackendUrl } from "@/lib/config";

export const OPERATOR_TOKEN_HEADER = "X-Release-Maintenance-Token";
const OPERATOR_TOKEN_STORAGE_KEY = "tickvault.operatorToken";

export interface CalendarHoliday {
  date: string;
  note: string;
}

export interface SpecialSession {
  date: string;
  open: string;
  close: string;
  token_fetch_at: string;
  capture_lead_minutes: number;
  note: string;
}

export interface SpecialSessionInput {
  date: string;
  open: string;
  close: string;
  token_fetch_at?: string;
  capture_lead_minutes: number;
  note: string;
}

export interface CalendarToday {
  date: string;
  local_time: string;
  is_trading_day: boolean;
  market_phase: string;
  special_session: SpecialSession | null;
  token_window: [string, string];
  capture_window: [string, string];
  token_ready: boolean;
  token_fetched_at: number | null;
  capture_running: boolean;
  automation: { phase?: string; last_action?: string | null; last_error?: string | null } | null;
}

export interface MarketCalendar {
  holidays: CalendarHoliday[];
  special_sessions: SpecialSession[];
  env_holidays: string[];
  load_errors?: string[];
  regular_hours: {
    market_open: string;
    market_close: string;
    capture_window: [string, string];
    token_window: [string, string];
  };
  today: CalendarToday;
}

function isMarketCalendar(value: unknown): value is MarketCalendar {
  if (value === null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    Array.isArray(record.holidays) &&
    Array.isArray(record.special_sessions) &&
    Array.isArray(record.env_holidays) &&
    record.today !== null &&
    typeof record.today === "object" &&
    record.regular_hours !== null &&
    typeof record.regular_hours === "object"
  );
}

/**
 * The operator key is never stored: it is typed for one change and then cleared.
 * Removes a key that an earlier release kept in this tab's session storage.
 */
export function forgetStoredOperatorToken(): void {
  try {
    window.sessionStorage.removeItem(OPERATOR_TOKEN_STORAGE_KEY);
  } catch {
    /* storage unavailable: nothing was stored */
  }
}

async function calendarRequest(
  path: string,
  init: RequestInit & { operatorToken?: string } = {},
): Promise<MarketCalendar> {
  const { operatorToken, headers, ...rest } = init;
  const res = await fetch(`${getBackendUrl()}/api/calendar${path}`, {
    cache: "no-store",
    ...rest,
    headers: {
      ...(rest.body ? { "Content-Type": "application/json" } : {}),
      ...(operatorToken ? { [OPERATOR_TOKEN_HEADER]: operatorToken } : {}),
      ...headers,
    },
  });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Some fields are invalid. Check the dates and HH:MM times."
          : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, res.status === 401 ? "Operator key was rejected." : message);
  }
  if (!isMarketCalendar(body)) throw new ApiError(502, "Calendar response was malformed.");
  return body;
}

export function getMarketCalendar(signal?: AbortSignal): Promise<MarketCalendar> {
  return calendarRequest("", { signal });
}

export function addHoliday(token: string, holiday: CalendarHoliday): Promise<MarketCalendar> {
  return calendarRequest("/holidays", {
    method: "POST",
    body: JSON.stringify(holiday),
    operatorToken: token,
  });
}

export function removeHoliday(token: string, date: string): Promise<MarketCalendar> {
  return calendarRequest(`/holidays/${encodeURIComponent(date)}`, {
    method: "DELETE",
    operatorToken: token,
  });
}

export function saveSpecialSession(
  token: string,
  session: SpecialSessionInput,
): Promise<MarketCalendar> {
  const body = { ...session, token_fetch_at: session.token_fetch_at || undefined };
  return calendarRequest("/special-sessions", {
    method: "PUT",
    body: JSON.stringify(body),
    operatorToken: token,
  });
}

export function removeSpecialSession(token: string, date: string): Promise<MarketCalendar> {
  return calendarRequest(`/special-sessions/${encodeURIComponent(date)}`, {
    method: "DELETE",
    operatorToken: token,
  });
}

export function fetchTokenNow(token: string): Promise<MarketCalendar> {
  return calendarRequest("/fetch-token", { method: "POST", operatorToken: token });
}

/** "HH:MM" minus ``minutes``, clamped at midnight. */
export function minusMinutes(hhmm: string, minutes: number): string {
  const match = /^(\d{2}):(\d{2})$/.exec(hhmm);
  if (!match) return "";
  const total = Math.max(0, Number(match[1]) * 60 + Number(match[2]) - minutes);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function formatDay(isoDate: string): string {
  const parsed = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return isoDate;
  return parsed.toLocaleDateString("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}
