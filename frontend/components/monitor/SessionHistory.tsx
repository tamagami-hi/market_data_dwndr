"use client";

import { memo, useState } from "react";

import { LossCauseBar, LossCauseLegend } from "@/components/monitor/LossCauseBar";
import { SessionDetails } from "@/components/monitor/SessionDetails";
import { Panel } from "@/components/ui/Panel";
import { ResponsiveDisclosure } from "@/components/ui/ResponsiveDisclosure";
import { StateMessage } from "@/components/ui/StateMessage";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { useSessionPages, type SessionPages } from "@/hooks/useSessionPages";
import type { SessionSummary } from "@/lib/api";
import {
  LOSS_CAUSE_STYLE,
  TONE_TEXT,
  activeLossCauses,
  lossCauses,
} from "@/lib/monitor/lossCauses";
import {
  OUTCOME_LABEL,
  OUTCOME_SEVERITY,
  completenessTone,
  primaryCompleteness,
  specialSessionLabel,
} from "@/lib/monitor/sessionOutcome";
import {
  formatBytes,
  formatIndianNumber,
  formatPercent,
  formatSecondsCompact,
  formatUptime,
} from "@/lib/numberFormat";

const DESKTOP_COLUMNS = 11;

/**
 * A right-aligned counter header whose visible text is abbreviated.
 *
 * The counter columns hold small integers, so the full word was setting the column width
 * and pushing the table into horizontal scroll. Screen readers still get the full name;
 * pointer users get it from the tooltip.
 */
function TightColumnHeader({ label, shortLabel }: { label: string; shortLabel: string }) {
  return (
    <th className="text-right" title={label}>
      <span aria-hidden="true">{shortLabel}</span>
      <span className="sr-only">{label}</span>
    </th>
  );
}

/**
 * Missing seconds with the stacked cause bar beneath. The causes come from the record's
 * own breakdown and reconcile to ``missing_seconds``; records written before that
 * breakdown existed show "--" instead of a fabricated zero.
 */
function MissingCell({ session }: { session: SessionSummary }) {
  if (session.missing_seconds === undefined) {
    return <span title="Recorded before the scheduled loss breakdown existed">--</span>;
  }
  const causes = lossCauses(session) ?? [];
  return (
    <>
      <span className={session.missing_seconds > 0 ? "text-danger" : "text-muted"}>
        {formatSecondsCompact(session.missing_seconds)}
      </span>
      <LossCauseBar causes={causes} missingSeconds={session.missing_seconds} />
    </>
  );
}

/** The day's primary figure: captured over scheduled seconds (see sessionOutcome.ts). */
function CompletenessValue({ session }: { session: SessionSummary }) {
  const pct = primaryCompleteness(session);
  return <span className={TONE_TEXT[completenessTone(pct)]}>{formatPercent(pct, 2)}</span>;
}

/** Outcome chip and, for an operator special session, its hours. */
function SessionTags({ session }: { session: SessionSummary }) {
  const special = specialSessionLabel(session.session_window);
  if (!session.outcome && !special) return null;
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs font-normal">
      {session.outcome && (
        <StatusIndicator
          compact
          label={OUTCOME_LABEL[session.outcome]}
          severity={OUTCOME_SEVERITY[session.outcome]}
        />
      )}
      {special && <span className="text-warning">{special}</span>}
    </span>
  );
}

/** Colour key for the cause bars, limited to the causes that occur in the listed rows. */
function CauseKey({ rows }: { rows: SessionSummary[] }) {
  const present = new Set(rows.flatMap((row) => activeLossCauses(row).map((cause) => cause.key)));
  const causes = LOSS_CAUSE_STYLE.filter((style) => present.has(style.key)).map((style) => ({
    ...style,
    seconds: 0,
  }));
  if (causes.length === 0) return null;
  return (
    <div className="border-b border-border px-3 py-1.5">
      <LossCauseLegend causes={causes} showSeconds={false} label="Missing-time cause key" />
    </div>
  );
}

function MobileSummary({ session, isLive }: { session: SessionSummary; isLive: boolean }) {
  return (
    <div className="text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-primary">
          {session.trading_date}
          {isLive && <span className="ml-2 status-success">live</span>}
          <SessionTags session={session} />
        </span>
        <span className="text-secondary" title="Process span: wall-clock from the first capture start">
          span {formatUptime(session.uptime_ms)}
        </span>
      </div>
      <div className="mt-1 grid grid-cols-4 gap-2 text-muted">
        <span><CompletenessValue session={session} /> captured</span>
        <span>
          {session.missing_seconds === undefined
            ? "--"
            : formatSecondsCompact(session.missing_seconds)}{" "}
          missing
        </span>
        <span>{session.grid_gaps} gaps</span>
        <span>{session.stale_seconds} stale s</span>
      </div>
      {session.missing_seconds !== undefined && (
        <LossCauseBar causes={lossCauses(session) ?? []} missingSeconds={session.missing_seconds} />
      )}
    </div>
  );
}

function counterClass(value: number, tone: "danger" | "warning"): string {
  if (!value) return "text-right text-muted";
  return tone === "danger" ? "text-right text-danger" : "text-right text-warning";
}

function DesktopRow({
  session,
  isLive,
  isOpen,
  onToggle,
}: {
  session: SessionSummary;
  isLive: boolean;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr>
        <td className="font-semibold text-primary">
          <button
            type="button"
            className="text-left hover:text-accent"
            aria-expanded={isOpen}
            aria-controls={`session-row-${session.trading_date}`}
            onClick={onToggle}
          >
            {session.trading_date}
          </button>
          {isLive && <span className="ml-2 text-xs status-success">live</span>}
          {session.exhausted && <span className="ml-2 text-xs text-danger">stalled</span>}
          <SessionTags session={session} />
        </td>
        <td className="text-right font-mono text-secondary">
          {formatIndianNumber(session.frames_written, 0)}
        </td>
        <td className="text-right font-mono">
          <CompletenessValue session={session} />
        </td>
        <td className="min-w-20 text-right font-mono">
          <MissingCell session={session} />
        </td>
        <td className={counterClass(session.grid_gaps, "danger")}>{session.grid_gaps}</td>
        <td className={counterClass(session.stale_seconds, "danger")}>{session.stale_seconds}</td>
        <td className={counterClass(session.dropped_batches, "danger")}>{session.dropped_batches}</td>
        <td className={counterClass(session.unmatched_ticks, "warning")}>{session.unmatched_ticks}</td>
        <td className={counterClass(session.reconnects, "warning")}>{session.reconnects}</td>
        <td className="text-right font-mono text-secondary">{formatUptime(session.uptime_ms)}</td>
        <td className="text-right font-mono text-secondary">{formatBytes(session.disk_bytes)}</td>
      </tr>
      {isOpen && (
        <tr id={`session-row-${session.trading_date}`}>
          <td colSpan={DESKTOP_COLUMNS} className="bg-surface-2">
            <SessionDetails session={session} />
          </td>
        </tr>
      )}
    </>
  );
}

/** "N of TOTAL sessions" and the button that pages in the next older batch. */
function OlderSessions({ pages, shown }: { pages: SessionPages; shown: number }) {
  if (!pages.hasMore && !pages.error) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-1.5 text-xs">
      <span className="text-muted" aria-live="polite">
        {pages.error ? (
          <span className="text-danger">{pages.error}</span>
        ) : pages.total === null ? (
          `${shown} most recent sessions`
        ) : (
          `${shown} of ${pages.total} sessions`
        )}
      </span>
      {pages.hasMore && (
        <button
          type="button"
          className="control min-h-11 px-3 text-xs text-accent hover:bg-surface-2"
          onClick={pages.loadOlder}
          disabled={pages.isLoading}
        >
          {pages.isLoading ? "Loading…" : "Load older sessions"}
        </button>
      )}
    </div>
  );
}

export const SessionHistory = memo(function SessionHistory({
  sessions,
  liveSession = null,
}: {
  sessions: SessionSummary[];
  /** In-progress session built from live telemetry; prepended while capture runs. */
  liveSession?: SessionSummary | null;
}) {
  const [openDates, setOpenDates] = useState<ReadonlySet<string>>(() => new Set());
  // /api/stats carries the recent sessions; older ones are paged in on request.
  const pages = useSessionPages(sessions);
  const recorded = pages.sessions;
  // The recorded log only gains a session after finalization, so while capture runs
  // merge a live row whose Frames value tracks the frames actually written to the
  // .bin files, in place of any recorded row for the same date.
  const rows = liveSession
    ? [liveSession, ...recorded.filter((s) => s.trading_date !== liveSession.trading_date)]
    : recorded;
  const isLive = (session: SessionSummary) =>
    liveSession !== null && session.trading_date === liveSession.trading_date;
  const toggle = (date: string) =>
    setOpenDates((current) => {
      const next = new Set(current);
      if (next.has(date)) next.delete(date);
      else next.add(date);
      return next;
    });

  return (
    <Panel
      title="Session history"
      subtitle={
        pages.total === null
          ? `${recorded.length} recorded sessions`
          : `${recorded.length} of ${pages.total} sessions`
      }
    >
      {rows.length === 0 ? (
        <div className="p-3">
          <StateMessage title="No completed sessions">A session record appears after capture finalization.</StateMessage>
        </div>
      ) : (
        <>
          <CauseKey rows={rows} />
          <div className="space-y-2 p-2 md:hidden">
            {rows.map((session) => (
              <ResponsiveDisclosure
                key={session.trading_date}
                id={`session-${session.trading_date}`}
                label={`${session.trading_date} session details`}
                summary={<MobileSummary session={session} isLive={isLive(session)} />}
              >
                <SessionDetails session={session} />
              </ResponsiveDisclosure>
            ))}
          </div>
          <div className="monitor-session-scroll hidden min-h-0 flex-1 overflow-auto md:block">
            <table className="data-table monitor-session-table tabular-nums">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="text-left">Session</th>
                  <th className="text-right">Frames</th>
                  <th className="text-right" title="Captured over scheduled seconds">
                    Completeness
                  </th>
                  <th className="text-right" title="Scheduled seconds with no frame, split by cause">
                    Missing
                  </th>
                  <th className="text-right">Gaps</th>
                  <th className="text-right">Stale s</th>
                  <TightColumnHeader label="Dropped" shortLabel="Drop" />
                  <TightColumnHeader label="Unmatched" shortLabel="Unmatch" />
                  <TightColumnHeader label="Reconnects" shortLabel="Recon" />
                  <TightColumnHeader label="Process span" shortLabel="Span" />
                  <th className="text-right">Disk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((session) => (
                  <DesktopRow
                    key={session.trading_date}
                    session={session}
                    isLive={isLive(session)}
                    isOpen={openDates.has(session.trading_date)}
                    onToggle={() => toggle(session.trading_date)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <OlderSessions pages={pages} shown={recorded.length} />
        </>
      )}
    </Panel>
  );
});
