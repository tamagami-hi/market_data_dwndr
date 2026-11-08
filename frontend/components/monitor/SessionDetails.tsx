import { SessionIncidents } from "@/components/monitor/SessionIncidents";
import type { SessionSummary } from "@/lib/api";
import { lossCauses, scheduleCompleteness, scheduleLoss, type LossCause } from "@/lib/monitor/lossCauses";
import { specialSessionLabel } from "@/lib/monitor/sessionOutcome";
import {
  formatBytes,
  formatIndianNumber,
  formatPercent,
  formatSecondsCompact,
  formatUptime,
} from "@/lib/numberFormat";

type Field = readonly [label: string, value: string];

function capitalized(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function optionalCount(value: number | undefined): string {
  return value === undefined ? "--" : formatIndianNumber(value, 0);
}

function causeFields(causes: LossCause[] | null): Field[] {
  return (causes ?? []).map((cause) => [capitalized(cause.label), formatSecondsCompact(cause.seconds)] as const);
}

function recoveryField(session: SessionSummary): Field[] {
  if (session.recovery_abandoned) return [["Recovery", "abandoned"]];
  if (session.exhausted) return [["Recovery", "exhausted"]];
  return [];
}

function sessionFields(session: SessionSummary): Field[] {
  return [
    ["Loss vs schedule", formatPercent(scheduleLoss(session), 2)],
    [
      "Missing",
      session.missing_seconds === undefined ? "--" : formatSecondsCompact(session.missing_seconds),
    ],
    ...causeFields(lossCauses(session)),
    [
      "Frames written / expected",
      `${formatIndianNumber(session.frames_written, 0)} / ${formatIndianNumber(session.frames_expected, 0)}`,
    ],
    ["Frame loss (all streams)", formatPercent(session.frame_loss_pct, 2)],
    ["Loss vs observed window", formatPercent(session.data_loss_pct, 2)],
    ["Gap events", formatIndianNumber(session.grid_gaps, 0)],
    ["Stale spells", formatIndianNumber(session.stale_events, 0)],
    ["Longest stale spell", formatSecondsCompact(session.longest_stale_spell_seconds ?? 0)],
    ["WS connect attempts", optionalCount(session.ws_connect_attempts)],
    ["In-process reconnects", optionalCount(session.in_process_reconnects)],
    ["Reconnects", formatIndianNumber(session.reconnects, 0)],
    ...recoveryField(session),
    ["Dropped batches", formatIndianNumber(session.dropped_batches, 0)],
    ["Drop rate", formatPercent(session.drop_rate_pct, 3)],
    ["Unmatched ticks", formatIndianNumber(session.unmatched_ticks, 0)],
    ["Token refreshes", formatIndianNumber(session.token_refreshes, 0)],
    // Wall-clock from the day's first capture start to the record, so it spans restarts
    // (and, for a process left running, days). It is not time spent capturing.
    ["Process span", formatUptime(session.uptime_ms)],
    ["Disk", formatBytes(session.disk_bytes)],
  ];
}

/** The backend's plain-language account of the day, biggest cause first. */
function Explanation({ lines }: { lines: string[] | undefined }) {
  if (!lines || lines.length === 0) return null;
  return (
    <ul className="space-y-0.5 text-xs" aria-label="What happened">
      {lines.map((line, index) => (
        <li key={`${index}-${line}`} className={index === 0 ? "font-semibold text-primary" : "text-secondary"}>
          {line}
        </li>
      ))}
    </ul>
  );
}

function telemetryCompleteness(session: SessionSummary): string {
  const pct = session.completeness_pct ?? scheduleCompleteness(session);
  if (pct === null || pct === undefined) return "--";
  const basis =
    session.completeness_basis_seconds ||
    session.scheduled_seconds_elapsed ||
    session.scheduled_seconds ||
    0;
  const captured = session.captured_seconds;
  return captured === undefined || basis <= 0
    ? formatPercent(pct, 2)
    : `${formatPercent(pct, 2)} (${formatIndianNumber(captured, 0)} / ${formatIndianNumber(basis, 0)}s)`;
}

function archiveCompleteness(session: SessionSummary): string {
  const archive = session.archive_reconciliation;
  if (!archive) return "not reconciled";
  const worst = archive.worst_artifact ? `worst ${archive.worst_artifact}, ` : "";
  return `${formatPercent(archive.completeness_pct, 2)} (${worst}${formatIndianNumber(
    archive.gap_count,
    0,
  )} gaps, ${formatIndianNumber(archive.artifact_count, 0)} artifacts)`;
}

/** Telemetry (what the process counted) beside archive truth (what is on disk). */
function CompletenessComparison({ session }: { session: SessionSummary }) {
  const special = specialSessionLabel(session.session_window);
  const fields: Field[] = [
    ["Telemetry completeness", telemetryCompleteness(session)],
    ["Archive completeness", archiveCompleteness(session)],
  ];
  return (
    <div className="text-xs">
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2" aria-label="Completeness">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="mt-0.5 font-mono text-primary">{value}</dd>
          </div>
        ))}
      </dl>
      {session.outcome_basis === "archive" && (
        <p className="mt-1 text-warning">The archive figure is worse and decides the outcome.</p>
      )}
      {special && (
        <p className="mt-1 text-secondary">
          {special}
          {session.session_window?.note ? ` · ${session.session_window.note}` : ""}
        </p>
      )}
    </div>
  );
}

export function SessionDetails({ session }: { session: SessionSummary }) {
  return (
    <div className="space-y-3 text-xs">
      <Explanation lines={session.explanation} />
      <CompletenessComparison session={session} />
      <SessionIncidents session={session} />
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border pt-2 sm:grid-cols-3 lg:grid-cols-4">
        {sessionFields(session).map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="mt-0.5 font-mono text-primary">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
