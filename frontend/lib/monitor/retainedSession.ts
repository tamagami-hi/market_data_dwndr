import { asRecord } from "@/lib/monitor/parse";
import type { Severity } from "@/lib/monitor/severity";

/** The parts of the calendar's ``today`` block the monitor needs to explain an idle day. */
export interface CalendarDay {
  date: string | null;
  isTradingDay: boolean;
  specialSession: { open: string; close: string } | null;
  captureWindow: readonly [string, string] | null;
  tokenReady: boolean | null;
}

function timeWindow(value: unknown): readonly [string, string] | null {
  return Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "string" &&
    typeof value[1] === "string"
    ? [value[0], value[1]]
    : null;
}

/** Narrow ``/api/calendar``'s ``today`` (untrusted JSON); null when it is unusable. */
export function normalizeCalendarDay(value: unknown): CalendarDay | null {
  const today = asRecord(value);
  if (!today || typeof today.is_trading_day !== "boolean") return null;
  const special = asRecord(today.special_session);
  return {
    date: typeof today.date === "string" ? today.date : null,
    isTradingDay: today.is_trading_day,
    specialSession:
      special && typeof special.open === "string" && typeof special.close === "string"
        ? { open: special.open, close: special.close }
        : null,
    captureWindow: timeWindow(today.capture_window),
    tokenReady: typeof today.token_ready === "boolean" ? today.token_ready : null,
  };
}

export interface RetainedNotice {
  title: string;
  detail: string;
  severity: Severity;
}

interface Reason {
  text: string;
  severity: Severity;
}

function preOpenReason(calendar: CalendarDay | null): Reason {
  const special = calendar?.specialSession;
  const start = calendar?.captureWindow?.[0];
  const parts = [
    special ? `special session ${special.open}–${special.close}` : null,
    start ? `capture starts ${start}` : "capture not started yet",
    calendar?.tokenReady === false ? "waiting for the broker token" : null,
  ].filter((part): part is string => part !== null);
  return { text: parts.join(", "), severity: "neutral" };
}

function todayReason(
  marketPhase: string | null,
  calendar: CalendarDay | null,
  isSameDay: boolean,
): Reason {
  if (marketPhase === "HOLIDAY" || calendar?.isTradingDay === false) {
    return { text: "market closed", severity: "neutral" };
  }
  if (marketPhase === "PRE_OPEN") return preOpenReason(calendar);
  if (marketPhase === "OPEN") {
    // Capture is owed right now and is not running: this is the crash / never-started case.
    return { text: "market open but capture is not running", severity: "danger" };
  }
  if (marketPhase === "CLOSED") {
    return isSameDay
      ? { text: "session closed", severity: "neutral" }
      : { text: "market closed and no session was captured today", severity: "warning" };
  }
  return { text: "capture not running", severity: "neutral" };
}

/**
 * The visible notice for a monitor that is showing a RETAINED snapshot rather than live
 * telemetry — e.g. Friday's session on a Sunday. Without it, Friday's frame rate, link
 * dots and heartbeat read as current. Null when the page is live.
 */
export function retainedSessionNotice(input: {
  retainedDate: string | null;
  tradingDate: string | null;
  marketPhase: string | null;
  calendar: CalendarDay | null;
}): RetainedNotice | null {
  const { retainedDate, tradingDate, marketPhase, calendar } = input;
  if (!retainedDate) return null;
  const isSameDay = tradingDate === null || retainedDate === tradingDate;
  const reason = todayReason(marketPhase, calendar, isSameDay);
  const title = isSameDay
    ? `Showing today's session (${retainedDate}) — ${reason.text}`
    : `Showing the last session (${retainedDate}) — today ${tradingDate}: ${reason.text}`;
  return {
    title,
    detail:
      "Capture is not running. Frame rate, links, heartbeat and tick rate below are from that session, not live.",
    severity: reason.severity,
  };
}
