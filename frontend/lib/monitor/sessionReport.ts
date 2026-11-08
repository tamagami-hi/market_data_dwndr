import {
  asRecord,
  optionalFinite,
  pickFinite,
  type UnknownRecord,
} from "@/lib/monitor/parse";
import type {
  ArchiveReconciliation,
  DatasetMissing,
  SessionEod,
  SessionLossCauses,
  SessionOutage,
  SessionOutcomeClass,
  SessionReportFields,
  SessionStop,
  SessionWindow,
} from "@/lib/sessionReport";

/**
 * Narrowing for the backend's per-session description (untrusted JSON). Each part is
 * validated on its own and dropped when malformed, so one bad field never blanks a row;
 * absent stays absent (a row recorded before the ledger has null counts, not zeros).
 */

const OUTCOMES = new Set<SessionOutcomeClass>([
  "complete",
  "degraded",
  "partial",
  "failed",
  "not_captured",
  "not_scheduled",
]);

const LOSS_CAUSE_KEYS = [
  "stale_feed_seconds",
  "awaiting_first_tick_seconds",
  "write_path_seconds",
  "unclassified_seconds",
  "restart_seconds",
  "restart_loop_seconds",
  "awaiting_token_seconds",
  "maintenance_seconds",
  "process_down_seconds",
] as const;

const COUNT_KEYS = [
  "completeness_pct",
  "completeness_basis_seconds",
  "archive_completeness_pct",
  "process_starts",
  "capture_starts",
  "failed_starts",
  "capture_failures",
  "process_exits",
  "eod_runs",
] as const;

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number {
  return optionalFinite(value) ?? 0;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function records<T>(value: unknown, parse: (record: UnknownRecord) => T | null): T[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap((item) => {
    const record = asRecord(item);
    const parsed = record ? parse(record) : null;
    return parsed === null ? [] : [parsed];
  });
}

function windowPairs(value: unknown): Array<readonly [number, number]> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((pair) => {
    if (!Array.isArray(pair) || pair.length !== 2) return [];
    const [start, end] = [optionalFinite(pair[0]), optionalFinite(pair[1])];
    return start === null || end === null ? [] : [[start, end] as const];
  });
}

function sessionWindow(value: unknown): SessionWindow | undefined {
  const w = asRecord(value);
  if (!w || typeof w.open !== "string" || typeof w.close !== "string") return undefined;
  return {
    open: w.open,
    close: w.close,
    is_special: w.is_special === true,
    note: str(w.note),
    trading_day: w.trading_day !== false,
    scheduled_seconds: num(w.scheduled_seconds),
    windows: windowPairs(w.windows),
  };
}

function archive(value: unknown): ArchiveReconciliation | undefined {
  const a = asRecord(value);
  if (!a || optionalFinite(a.completeness_pct) === null) return undefined;
  return {
    completeness_pct: num(a.completeness_pct),
    data_loss_pct: num(a.data_loss_pct),
    worst_artifact: str(a.worst_artifact),
    artifact_count: num(a.artifact_count),
    scheduled_seconds: num(a.scheduled_seconds),
    captured_seconds: num(a.captured_seconds),
    missing_seconds: num(a.missing_seconds),
    gap_count: num(a.gap_count),
    longest_gap_seconds: num(a.longest_gap_seconds),
  };
}

function lossCauses(value: unknown): SessionLossCauses | undefined {
  const causes = asRecord(value);
  if (!causes) return undefined;
  return Object.fromEntries(
    LOSS_CAUSE_KEYS.map((key) => [key, Math.max(0, num(causes[key]))]),
  ) as unknown as SessionLossCauses;
}

function outage(o: UnknownRecord): SessionOutage | null {
  const start = optionalFinite(o.start_at);
  const end = optionalFinite(o.end_at);
  if (typeof o.cause !== "string" || start === null || end === null) return null;
  return {
    cause: o.cause,
    start_at: start,
    end_at: end,
    recovered: o.recovered !== false,
    seconds: num(o.seconds),
    trigger: str(o.trigger),
    failed_starts: num(o.failed_starts),
    exceptions: strings(o.exceptions),
    skipped_indices: strings(o.skipped_indices),
  };
}

function datasetMissing(d: UnknownRecord): DatasetMissing | null {
  if (typeof d.dataset !== "string") return null;
  return {
    dataset: d.dataset,
    seconds: num(d.seconds),
    from_at: num(d.from_at),
    to_at: num(d.to_at),
    starts: num(d.starts),
    reason: str(d.reason),
    quote_error: str(d.quote_error),
  };
}

function stop(s: UnknownRecord): SessionStop | null {
  const at = optionalFinite(s.at);
  if (at === null) return null;
  return {
    at,
    reason: str(s.reason),
    clean: typeof s.clean === "boolean" ? s.clean : null,
    count: Math.max(1, num(s.count)),
  };
}

function eod(value: unknown): SessionEod | undefined {
  const e = asRecord(value);
  const at = optionalFinite(e?.at);
  if (!e || at === null) return undefined;
  return {
    at,
    status: str(e.status),
    files: optionalFinite(e.files),
    archive_data_loss_pct: optionalFinite(e.archive_data_loss_pct),
    worst_artifact: str(e.worst_artifact),
    artifact_count: optionalFinite(e.artifact_count),
  };
}

/** Drop undefined entries so absent fields stay absent on the spread result. */
function defined<T extends object>(fields: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(fields).filter(([, value]) => value !== undefined),
  ) as Partial<T>;
}

export function normalizeSessionReport(row: UnknownRecord): SessionReportFields {
  const outcome =
    typeof row.outcome === "string" && OUTCOMES.has(row.outcome as SessionOutcomeClass)
      ? (row.outcome as SessionOutcomeClass)
      : undefined;
  const basis = row.outcome_basis === "archive" || row.outcome_basis === "telemetry"
    ? row.outcome_basis
    : undefined;
  return {
    ...pickFinite(row, COUNT_KEYS),
    ...defined({
      final: typeof row.final === "boolean" ? row.final : undefined,
      outcome,
      outcome_basis: basis,
      archive_reconciliation: archive(row.archive_reconciliation),
      session_window: sessionWindow(row.session_window),
      incidents_recorded:
        typeof row.incidents_recorded === "boolean" ? row.incidents_recorded : undefined,
      loss_causes: lossCauses(row.loss_causes),
      outages: records(row.outages, outage),
      datasets_missing: records(row.datasets_missing, datasetMissing),
      stops: records(row.stops, stop),
      eod: eod(row.eod),
      explanation: Array.isArray(row.explanation) ? strings(row.explanation) : undefined,
    }),
  };
}
