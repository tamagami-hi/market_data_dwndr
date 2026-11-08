import { scheduleCompleteness, scheduleLoss } from "@/lib/monitor/lossCauses";
import type { Severity } from "@/lib/monitor/severity";
import type { SessionOutcomeClass, SessionWindow } from "@/lib/sessionReport";

/** Thresholds mirror backend ``OUTCOME_THRESHOLDS`` (app/ops/session_outcome.py). */
export const COMPLETE_MIN_PCT = 99.5;
export const DEGRADED_MIN_PCT = 95;

export const OUTCOME_LABEL: Record<SessionOutcomeClass, string> = {
  complete: "complete",
  degraded: "degraded",
  partial: "partial",
  failed: "failed",
  not_captured: "not captured",
  not_scheduled: "not scheduled",
};

export const OUTCOME_SEVERITY: Record<SessionOutcomeClass, Severity> = {
  complete: "success",
  degraded: "warning",
  partial: "danger",
  failed: "danger",
  not_captured: "danger",
  not_scheduled: "neutral",
};

/**
 * The day's primary completeness %: the backend's ``completeness_pct`` (captured over
 * scheduled seconds). Rows it has not described fall back to the same ratio computed
 * here, then to 100 - the schedule-relative loss for records older than both.
 */
export function primaryCompleteness(session: {
  completeness_pct?: number;
  captured_seconds?: number;
  scheduled_seconds?: number;
  scheduled_seconds_elapsed?: number;
  scheduled_loss_pct?: number;
  data_loss_pct: number;
}): number {
  return (
    session.completeness_pct ??
    scheduleCompleteness(session) ??
    Math.max(0, 100 - scheduleLoss(session))
  );
}

/** Colour for a completeness %, by the outcome thresholds and as displayed (2 dp). */
export function completenessTone(pct: number): Severity {
  const shown = Number(pct.toFixed(2));
  if (shown >= COMPLETE_MIN_PCT) return "neutral";
  if (shown >= DEGRADED_MIN_PCT) return "warning";
  return "danger";
}

/** "special session 18:00–19:15" for an operator special session, else null. */
export function specialSessionLabel(window: SessionWindow | undefined): string | null {
  if (!window?.is_special) return null;
  return `special session ${window.open}–${window.close}`;
}
