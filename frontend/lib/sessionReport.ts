/**
 * The per-session description the backend adds to every session-history row (and to
 * ``/api/stats.session_outcome`` for the monitor's date): one primary completeness
 * figure, an outcome class, every missing second attributed to one cause, and the
 * incident timeline behind it. See backend ``app/ops/session_outcome.py``.
 *
 * Every field is optional: a row the backend could not describe is returned as recorded.
 */

/** ``complete`` ≥ 99.5% · ``degraded`` ≥ 95% (or a dataset missing) · ``partial`` ≥ 50% · ``failed`` < 50%. */
export type SessionOutcomeClass =
  | "complete"
  | "degraded"
  | "partial"
  | "failed"
  | "not_captured"
  | "not_scheduled";

/** The window(s) the day was owed against: regular hours or an operator special session. */
export interface SessionWindow {
  open: string;
  close: string;
  is_special: boolean;
  note: string | null;
  trading_day: boolean;
  scheduled_seconds: number;
  windows: Array<readonly [number, number]>;
}

/** The archive (on-disk) completeness record for the day, from EOD reconciliation. */
export interface ArchiveReconciliation {
  completeness_pct: number;
  data_loss_pct: number;
  worst_artifact: string | null;
  artifact_count: number;
  scheduled_seconds: number;
  captured_seconds: number;
  missing_seconds: number;
  gap_count: number;
  longest_gap_seconds: number;
}

/** Every missing second, attributed once; the values sum to ``missing_seconds``. */
export interface SessionLossCauses {
  stale_feed_seconds: number;
  awaiting_first_tick_seconds: number;
  write_path_seconds: number;
  unclassified_seconds: number;
  restart_seconds: number;
  restart_loop_seconds: number;
  awaiting_token_seconds: number;
  maintenance_seconds: number;
  process_down_seconds: number;
}

/** A span of scheduled time with no capture running, and what the ledger says caused it. */
export interface SessionOutage {
  cause: string;
  start_at: number;
  end_at: number;
  /** False when the outage was still open at the end of the schedule (or now). */
  recovered: boolean;
  seconds: number;
  /** The failure that opened the outage (e.g. CaptureStalledError). */
  trigger: string | null;
  failed_starts: number;
  exceptions: string[];
  /** Index option chains capture started without during the outage. */
  skipped_indices: string[];
}

/** A dataset capture ran without, and for how many scheduled seconds. */
export interface DatasetMissing {
  dataset: string;
  seconds: number;
  from_at: number;
  to_at: number;
  starts: number;
  reason: string | null;
  quote_error: string | null;
}

export interface SessionStop {
  at: number;
  reason: string | null;
  clean: boolean | null;
  count: number;
}

export interface SessionEod {
  at: number;
  status: string | null;
  files: number | null;
  archive_data_loss_pct: number | null;
  worst_artifact: string | null;
  artifact_count: number | null;
}

export interface SessionReportFields {
  /** PRIMARY figure: captured / scheduled seconds (the elapsed part while the day runs). */
  completeness_pct?: number;
  completeness_basis_seconds?: number;
  /** True once the day's schedule has ended. */
  final?: boolean;
  outcome?: SessionOutcomeClass;
  /** Which figure decided the outcome: the archive wins when it is worse. */
  outcome_basis?: "telemetry" | "archive";
  archive_completeness_pct?: number;
  archive_reconciliation?: ArchiveReconciliation;
  session_window?: SessionWindow;
  /** False for days recorded before the incident ledger existed: counts are unknown. */
  incidents_recorded?: boolean;
  process_starts?: number;
  capture_starts?: number;
  failed_starts?: number;
  capture_failures?: number;
  process_exits?: number;
  loss_causes?: SessionLossCauses;
  outages?: SessionOutage[];
  datasets_missing?: DatasetMissing[];
  stops?: SessionStop[];
  eod?: SessionEod;
  eod_runs?: number;
  /** Plain-language sentences, biggest cause first. */
  explanation?: string[];
}
