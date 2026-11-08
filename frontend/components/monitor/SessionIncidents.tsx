import type { ReactNode } from "react";

import type { SessionSummary } from "@/lib/api";
import { LOSS_CAUSE_STYLE, type LossCauseKey } from "@/lib/monitor/lossCauses";
import {
  formatIndianNumber,
  formatIstClock,
  formatPercent,
  formatSecondsCompact,
} from "@/lib/numberFormat";
import type { DatasetMissing, SessionEod, SessionOutage, SessionStop } from "@/lib/sessionReport";

/** The day the incident ledger started recording; earlier rows have no counts at all. */
const LEDGER_FIRST_DAY = "2026-11-08";

/** Backend outage causes (incident_timeline.CAUSE_*) → the cause bar's keys. */
const OUTAGE_CAUSE_KEY: Record<string, LossCauseKey> = {
  restart_loop: "restart_loop",
  restart: "process_restart",
  awaiting_token: "awaiting_token",
  maintenance: "maintenance",
  process_down: "process_down",
};

function causeStyle(cause: string) {
  const key = OUTAGE_CAUSE_KEY[cause];
  return LOSS_CAUSE_STYLE.find((style) => style.key === key) ?? {
    label: cause.replace(/_/g, " "),
    color: "var(--text-muted)",
  };
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-1">
      <h3 className="text-xs font-semibold text-secondary">{title}</h3>
      {children}
    </section>
  );
}

function Span({ from, to }: { from: number; to: number }) {
  return (
    <span className="font-mono text-accent">
      {formatIstClock(from)} → {formatIstClock(to)}
    </span>
  );
}

function OutageItem({ outage }: { outage: SessionOutage }) {
  const style = causeStyle(outage.cause);
  return (
    <li className="space-y-0.5">
      <div className="flex flex-wrap items-center gap-x-2">
        <Span from={outage.start_at} to={outage.end_at} />
        <span className="font-mono text-primary">{formatSecondsCompact(outage.seconds)}</span>
        <span className="inline-flex items-center gap-1 text-primary">
          <span aria-hidden="true" className="inline-block size-2 rounded-sm" style={{ backgroundColor: style.color }} />
          {style.label}
        </span>
        {!outage.recovered && <span className="text-danger">not recovered</span>}
      </div>
      <div className="text-muted">
        {outage.trigger ? `after ${outage.trigger}` : "no recorded trigger"}
        {outage.failed_starts > 0 &&
          ` · ${formatIndianNumber(outage.failed_starts, 0)} failed start${outage.failed_starts === 1 ? "" : "s"}`}
        {outage.exceptions.length > 0 && ` (${outage.exceptions.join(", ")})`}
      </div>
      {outage.skipped_indices.length > 0 && (
        <div className="text-warning">
          index chains skipped: {outage.skipped_indices.join(", ")}
        </div>
      )}
    </li>
  );
}

function DatasetsMissingList({ datasets }: { datasets: DatasetMissing[] }) {
  // The six chains of one outage share one quote error; list it once, not six times.
  const errors = [...new Set(datasets.map((d) => d.quote_error).filter((e): e is string => !!e))];
  return (
    <Section title="Datasets missing">
      <ul className="space-y-0.5">
        {datasets.map((dataset) => (
          <li key={dataset.dataset} className="flex flex-wrap gap-x-2">
            <span className="font-semibold text-primary">{dataset.dataset}</span>
            <span className="font-mono text-primary">{formatSecondsCompact(dataset.seconds)}</span>
            <Span from={dataset.from_at} to={dataset.to_at} />
            <span className="text-muted">
              {formatIndianNumber(dataset.starts, 0)} start{dataset.starts === 1 ? "" : "s"}
              {dataset.reason ? ` · ${dataset.reason}` : ""}
            </span>
          </li>
        ))}
      </ul>
      {errors.map((error) => (
        <p key={error} className="break-all font-mono text-muted">quote error: {error}</p>
      ))}
    </Section>
  );
}

function stopText(stop: SessionStop): string {
  const reason = stop.reason ? stop.reason.replace(/_/g, " ") : "no reason recorded";
  const clean = stop.clean === null ? "" : stop.clean ? " · clean" : " · not clean";
  return `${reason}${clean}${stop.count > 1 ? ` · ×${stop.count}` : ""}`;
}

function eodText(eod: SessionEod, runs: number | undefined): string {
  const parts = [
    eod.status ?? "status unknown",
    eod.files === null ? null : `${formatIndianNumber(eod.files, 0)} files`,
    eod.archive_data_loss_pct === null
      ? null
      : `archive loss ${formatPercent(eod.archive_data_loss_pct, 2)}${
          eod.worst_artifact ? ` (worst ${eod.worst_artifact}${eod.artifact_count ? ` of ${eod.artifact_count}` : ""})` : ""
        }`,
    runs !== undefined && runs > 1 ? `${runs} runs` : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

function StopsAndEod({ session }: { session: SessionSummary }) {
  const stops = session.stops ?? [];
  if (stops.length === 0 && !session.eod) return null;
  return (
    <Section title="Stops and end of day">
      <ul className="space-y-0.5">
        {stops.map((stop) => (
          <li key={`stop-${stop.at}`} className="flex flex-wrap gap-x-2">
            <span className="font-mono text-accent">{formatIstClock(stop.at)}</span>
            <span className="text-secondary">stopped: {stopText(stop)}</span>
          </li>
        ))}
        {session.eod && (
          <li className="flex flex-wrap gap-x-2">
            <span className="font-mono text-accent">{formatIstClock(session.eod.at)}</span>
            <span className="text-secondary">EOD: {eodText(session.eod, session.eod_runs)}</span>
          </li>
        )}
      </ul>
    </Section>
  );
}

function IncidentCounts({ session }: { session: SessionSummary }) {
  const counts: Array<[string, number | undefined]> = [
    ["Process starts", session.process_starts],
    ["Capture starts", session.capture_starts],
    ["Failed starts", session.failed_starts],
    ["Capture failures", session.capture_failures],
    ["Process exits", session.process_exits],
    ["Escalations", session.escalations],
  ];
  return (
    <dl className="grid grid-cols-3 gap-x-4 gap-y-1 sm:grid-cols-6" aria-label="Incident counts">
      {counts.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted">{label}</dt>
          <dd className="font-mono text-primary">{value === undefined ? "--" : formatIndianNumber(value, 0)}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * What the incident ledger recorded for the day: outages with their cause and trigger,
 * datasets capture ran without, stops, EOD and the honest process counts. A day recorded
 * before the ledger existed says so instead of showing zeros.
 */
export function SessionIncidents({ session }: { session: SessionSummary }) {
  if (session.incidents_recorded === undefined) return null;
  if (!session.incidents_recorded) {
    const before = session.trading_date < LEDGER_FIRST_DAY ? ` (recorded before ${LEDGER_FIRST_DAY})` : "";
    return <p className="text-muted">No incident ledger for this date{before}.</p>;
  }
  const outages = session.outages ?? [];
  const datasets = (session.datasets_missing ?? []).filter((d) => d.seconds > 0);
  return (
    <div className="space-y-3">
      <IncidentCounts session={session} />
      <Section title="Outages">
        {outages.length === 0 ? (
          <p className="text-muted">No outage in the scheduled window.</p>
        ) : (
          <ol className="space-y-2">
            {outages.map((outage) => (
              <OutageItem key={`${outage.cause}-${outage.start_at}`} outage={outage} />
            ))}
          </ol>
        )}
      </Section>
      {datasets.length > 0 && <DatasetsMissingList datasets={datasets} />}
      <StopsAndEod session={session} />
    </div>
  );
}
