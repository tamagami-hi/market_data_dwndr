import type { Severity } from "@/lib/monitor/severity";
import type { SessionLossCauses } from "@/lib/sessionReport";

/**
 * Why scheduled seconds have no frame, split into causes that reconcile with the total.
 *
 * The backend counts each missed second once, in exactly one bucket, so the parts sum to
 * ``missing_seconds``. The display keeps that guarantee visible: anything the record does
 * not attribute is shown as its own part rather than silently widening another one.
 */
export interface LossCauseFields {
  missing_seconds?: number;
  stale_feed_seconds?: number;
  restart_downtime_seconds?: number;
  not_running_seconds?: number;
  downtime_seconds?: number;
  awaiting_first_tick_seconds?: number;
  write_path_seconds?: number;
  unclassified_seconds?: number;
  /** The backend's ledger-attributed split; preferred over the engine buckets above. */
  loss_causes?: SessionLossCauses;
}

export type LossCauseKey =
  | "stale_feed"
  | "restart_loop"
  | "process_restart"
  | "awaiting_token"
  | "maintenance"
  | "process_down"
  | "not_running"
  | "downtime"
  | "awaiting_first_tick"
  | "write_path"
  | "unclassified"
  | "unaccounted";

export interface LossCause {
  key: LossCauseKey;
  label: string;
  seconds: number;
  /** CSS colour (a design token) for the bar segment and legend swatch. */
  color: string;
}

const PROCESS_DOWN_COLOR = "color-mix(in srgb, var(--danger) 55%, var(--surface-3))";

/** Fixed order and colour per cause, so the bar reads the same on every row. */
export const LOSS_CAUSE_STYLE: ReadonlyArray<Omit<LossCause, "seconds">> = [
  { key: "stale_feed", label: "stale feed", color: "var(--warning)" },
  { key: "restart_loop", label: "restart loop", color: "var(--danger)" },
  { key: "process_restart", label: "process restart", color: "var(--marker-pain)" },
  {
    key: "awaiting_token",
    label: "awaiting token",
    color: "color-mix(in srgb, var(--warning) 50%, var(--danger))",
  },
  { key: "maintenance", label: "maintenance", color: "var(--text-secondary)" },
  { key: "process_down", label: "process down", color: PROCESS_DOWN_COLOR },
  { key: "not_running", label: "not running", color: PROCESS_DOWN_COLOR },
  { key: "downtime", label: "downtime (unsplit)", color: PROCESS_DOWN_COLOR },
  { key: "awaiting_first_tick", label: "awaiting first tick", color: "var(--accent)" },
  { key: "write_path", label: "write path", color: "var(--marker-atm)" },
  { key: "unclassified", label: "unclassified", color: "var(--text-muted)" },
  { key: "unaccounted", label: "unaccounted", color: "var(--border-strong)" },
];

/** Causes that only appear when non-zero: residuals, not categories worth a "0s" line. */
const RESIDUAL_CAUSES = new Set<LossCauseKey>(["downtime", "unaccounted"]);

function seconds(value: number | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

/** The backend's ledger split: restart loop, token wait and maintenance are their own causes. */
function fromLedger(causes: SessionLossCauses): Partial<Record<LossCauseKey, number>> {
  return {
    stale_feed: seconds(causes.stale_feed_seconds),
    restart_loop: seconds(causes.restart_loop_seconds),
    process_restart: seconds(causes.restart_seconds),
    awaiting_token: seconds(causes.awaiting_token_seconds),
    maintenance: seconds(causes.maintenance_seconds),
    process_down: seconds(causes.process_down_seconds),
    awaiting_first_tick: seconds(causes.awaiting_first_tick_seconds),
    write_path: seconds(causes.write_path_seconds),
    unclassified: seconds(causes.unclassified_seconds),
  };
}

/** The engine's own buckets (records and live payloads without ``loss_causes``). */
function fromEngine(fields: LossCauseFields): Partial<Record<LossCauseKey, number>> {
  const restart = seconds(fields.restart_downtime_seconds);
  const notRunning = seconds(fields.not_running_seconds);
  return {
    stale_feed: seconds(fields.stale_feed_seconds),
    process_restart: restart,
    not_running: notRunning,
    downtime: Math.max(0, seconds(fields.downtime_seconds) - restart - notRunning),
    awaiting_first_tick: seconds(fields.awaiting_first_tick_seconds),
    write_path: seconds(fields.write_path_seconds),
    unclassified: seconds(fields.unclassified_seconds),
  };
}

/**
 * Every cause that applies to the record, in display order, zero-valued ones included.
 *
 * With the backend's ``loss_causes`` the not-running time is split by the incident ledger
 * (restart loop / restart / awaiting token / maintenance / process down). Without it the
 * engine buckets are used, and ``downtime`` only appears for the part of
 * ``downtime_seconds`` the record did not split into restart vs not running.
 * ``unaccounted`` only appears when the stated causes fall short of ``missing_seconds``.
 * Returns null when the record has no scheduled breakdown at all.
 */
export function lossCauses(fields: LossCauseFields): LossCause[] | null {
  if (typeof fields.missing_seconds !== "number") return null;
  const parts = fields.loss_causes ? fromLedger(fields.loss_causes) : fromEngine(fields);
  const attributed = Object.values(parts).reduce((sum, value) => sum + (value ?? 0), 0);
  const withRemainder = {
    ...parts,
    unaccounted: Math.max(0, seconds(fields.missing_seconds) - attributed),
  };
  return LOSS_CAUSE_STYLE.flatMap((style) => {
    const value = withRemainder[style.key];
    if (value === undefined) return [];
    if (RESIDUAL_CAUSES.has(style.key) && value <= 0) return [];
    return [{ ...style, seconds: value }];
  });
}

/** Causes that actually cost time. */
export function activeLossCauses(fields: LossCauseFields): LossCause[] {
  return (lossCauses(fields) ?? []).filter((cause) => cause.seconds > 0);
}

/**
 * Schedule-relative loss: owed seconds with no frame, including time the process was not
 * running at all. Records written before the scheduled figures existed fall back to the
 * process-relative ``data_loss_pct``.
 */
export function scheduleLoss(session: {
  scheduled_loss_pct?: number;
  data_loss_pct: number;
}): number {
  return session.scheduled_loss_pct ?? session.data_loss_pct;
}

/**
 * Captured share of the scheduled session, from the seconds themselves. The elapsed part
 * of the schedule is the denominator when known (it equals the full schedule once a
 * session closes, and keeps a live row from reading low mid-day). Null when the record
 * predates the scheduled breakdown.
 */
export function scheduleCompleteness(session: {
  captured_seconds?: number;
  scheduled_seconds?: number;
  scheduled_seconds_elapsed?: number;
}): number | null {
  if (typeof session.captured_seconds !== "number") return null;
  const elapsed = session.scheduled_seconds_elapsed ?? 0;
  const denominator = elapsed > 0 ? elapsed : (session.scheduled_seconds ?? 0);
  if (denominator <= 0) return null;
  return Math.min(100, (session.captured_seconds / denominator) * 100);
}

/**
 * Colour for a loss percentage, keyed to the value AS DISPLAYED at ``decimals``: 0.004%
 * printed as "0.00%" must not be amber, or the colour contradicts the number beside it.
 */
export function lossTone(value: number, decimals = 2): Severity {
  const shown = Number(value.toFixed(decimals));
  if (shown >= 1) return "danger";
  if (shown > 0) return "warning";
  return "neutral";
}

export const TONE_TEXT: Record<Severity, string> = {
  neutral: "text-muted",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};
