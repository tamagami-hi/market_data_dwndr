import type { SessionIncident, SessionSummary } from "@/lib/api";
import {
  lossCauses,
  scheduleCompleteness,
  scheduleLoss,
  type LossCause,
} from "@/lib/monitor/lossCauses";
import {
  formatBytes,
  formatClockTime,
  formatIndianNumber,
  formatPercent,
  formatSecondsCompact,
  formatUptime,
} from "@/lib/numberFormat";

type Field = readonly [label: string, value: string];

/** Causes always listed, even at 0s, so "no restart time" is stated rather than implied. */
const CORE_CAUSES = new Set([
  "stale_feed",
  "process_restart",
  "not_running",
  "awaiting_first_tick",
  "write_path",
  "unclassified",
]);

function capitalized(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function optionalCount(value: number | undefined): string {
  return value === undefined ? "--" : formatIndianNumber(value, 0);
}

function completenessField(session: SessionSummary): Field {
  const completeness = scheduleCompleteness(session);
  if (completeness === null) return ["Completeness", "--"];
  const denominator = session.scheduled_seconds_elapsed || session.scheduled_seconds || 0;
  return [
    "Completeness",
    `${formatPercent(completeness, 2)} (${formatIndianNumber(session.captured_seconds ?? 0, 0)} / ${formatIndianNumber(denominator, 0)}s)`,
  ];
}

function causeFields(causes: LossCause[] | null): Field[] {
  if (!causes) return [];
  return causes
    .filter((cause) => CORE_CAUSES.has(cause.key) || cause.seconds > 0)
    .map((cause) => [capitalized(cause.label), formatSecondsCompact(cause.seconds)] as const);
}

function recoveryField(session: SessionSummary): Field[] {
  if (session.recovery_abandoned) return [["Recovery", "abandoned"]];
  if (session.exhausted) return [["Recovery", "exhausted"]];
  return [];
}

function sessionFields(session: SessionSummary): Field[] {
  return [
    // EXTENSION POINT: the backend's per-session verdict, rendered only when present.
    ...(session.outcome
      ? [["Outcome", [session.outcome.label, session.outcome.detail].filter(Boolean).join(": ")] as const]
      : []),
    completenessField(session),
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
    ["Restart escalations", formatIndianNumber(session.escalations ?? 0, 0)],
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

/** EXTENSION POINT: incident timeline, rendered only when the backend supplies one. */
function IncidentList({ incidents }: { incidents: SessionIncident[] }) {
  return (
    <ol className="mt-3 space-y-1 border-t border-border pt-2 text-xs" aria-label="Incidents">
      {incidents.map((incident) => (
        <li key={`${incident.start_ms}-${incident.cause}`} className="grid grid-cols-[auto_1fr] gap-2">
          <time className="font-mono text-accent">
            {formatClockTime(incident.start_ms)}
            {incident.end_ms ? `–${formatClockTime(incident.end_ms)}` : ""}
          </time>
          <span className="text-secondary">
            {incident.cause}
            {incident.seconds !== undefined && ` · ${formatSecondsCompact(incident.seconds)}`}
            {incident.detail && <span className="ml-1 text-muted">{incident.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function SessionDetails({ session }: { session: SessionSummary }) {
  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3 lg:grid-cols-4">
        {sessionFields(session).map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="mt-0.5 font-mono text-primary">{value}</dd>
          </div>
        ))}
      </dl>
      {session.incidents && session.incidents.length > 0 && (
        <IncidentList incidents={session.incidents} />
      )}
    </div>
  );
}
