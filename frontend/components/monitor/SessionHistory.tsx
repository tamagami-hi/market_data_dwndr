import { memo } from "react";

import { Panel } from "@/components/ui/Panel";
import { ResponsiveDisclosure } from "@/components/ui/ResponsiveDisclosure";
import { StateMessage } from "@/components/ui/StateMessage";
import type { SessionSummary } from "@/lib/api";
import { formatBytes, formatIndianNumber, formatPercent, formatUptime } from "@/lib/numberFormat";

function SessionDetails({ session }: { session: SessionSummary }) {
  const uptimeSeconds = (session.uptime_ms ?? 0) / 1000;
  const averageTicks = uptimeSeconds >= 1 ? session.ticks_received / uptimeSeconds : 0;
  const fields = [
    ["Frames written", formatIndianNumber(session.frames_written, 0)],
    ["Frames expected", formatIndianNumber(session.frames_expected, 0)],
    ["Full-session loss", formatPercent(session.frame_loss_pct, 2)],
    ["Stale spells", formatIndianNumber(session.stale_events, 0)],
    ["Elapsed seconds", formatIndianNumber(session.grid_seconds_elapsed, 0)],
    ["Dropped batches", formatIndianNumber(session.dropped_batches, 0)],
    ["Drop rate", formatPercent(session.drop_rate_pct, 3)],
    ["Unmatched ticks", formatIndianNumber(session.unmatched_ticks, 0)],
    ["Average ticks / sec", formatIndianNumber(averageTicks, 0)],
    ["Reconnects", formatIndianNumber(session.reconnects, 0)],
    ["Restart escalations", formatIndianNumber(session.escalations ?? 0, 0)],
    ["Longest stale spell", `${formatIndianNumber(session.longest_stale_spell_seconds ?? 0, 0)}s`],
    ["Token refreshes", formatIndianNumber(session.token_refreshes, 0)],
    ["Disk", formatBytes(session.disk_bytes)],
    [
      "State",
      session.recovery_abandoned
        ? "recovery abandoned"
        : session.exhausted
          ? "recovery exhausted"
          : "complete",
    ],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
      {fields.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted">{label}</dt>
          <dd className="mt-0.5 font-mono text-primary">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

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

export const SessionHistory = memo(function SessionHistory({
  sessions,
  liveSession = null,
}: {
  sessions: SessionSummary[];
  /** In-progress session built from live telemetry; prepended while capture runs. */
  liveSession?: SessionSummary | null;
}) {
  // The recorded log only gains a session after finalization, so while capture runs
  // merge a live row whose Frames value tracks the frames actually written to the
  // .bin files. A recorded row for the same date wins (it is the finalized truth),
  // so filter it out to avoid showing the date twice.
  const rows = liveSession
    ? [liveSession, ...sessions.filter((s) => s.trading_date !== liveSession.trading_date)]
    : sessions;
  const isLive = (session: SessionSummary) =>
    liveSession !== null && session.trading_date === liveSession.trading_date;
  return (
    <Panel title="Session history" subtitle={`${sessions.length} recorded sessions`}>
      {rows.length === 0 ? (
        <div className="p-3">
          <StateMessage title="No completed sessions">A session record appears after capture finalization.</StateMessage>
        </div>
      ) : (
        <>
          <div className="space-y-2 p-2 md:hidden">
            {rows.map((session) => (
              <ResponsiveDisclosure
                key={session.trading_date}
                id={`session-${session.trading_date}`}
                label={`${session.trading_date} session details`}
                summary={
                  <div className="text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-primary">
                        {session.trading_date}
                        {isLive(session) && <span className="ml-2 status-success">live</span>}
                      </span>
                      <span className={session.exhausted ? "text-danger" : "text-secondary"}>
                        {formatUptime(session.uptime_ms)}
                      </span>
                    </div>
                    <div className="mt-1 grid grid-cols-4 gap-2 text-muted">
                      <span>{formatPercent(session.data_loss_pct, 2)} loss</span>
                      <span>{session.grid_seconds_lost} lost s</span>
                      <span>{session.grid_gaps} gaps</span>
                      <span>{session.stale_seconds} stale s</span>
                    </div>
                  </div>
                }
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
                  <th className="text-right">Data loss</th>
                  <th className="text-right">Gap loss</th>
                  <th className="text-right">Lost s</th>
                  <th className="text-right">Gaps</th>
                  <th className="text-right">Stale s</th>
                  <TightColumnHeader label="Dropped" shortLabel="Drop" />
                  <TightColumnHeader label="Unmatched" shortLabel="Unmatch" />
                  <TightColumnHeader label="Reconnects" shortLabel="Recon" />
                  <th className="text-right">Ticks/s</th>
                  <th className="text-right">Uptime</th>
                  <th className="text-right">Disk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((session) => (
                  <tr key={session.trading_date}>
                    <td className="font-semibold text-primary">
                      {session.trading_date}
                      {isLive(session) && <span className="ml-2 text-xs status-success">live</span>}
                      {session.exhausted && <span className="ml-2 text-xs text-danger">stalled</span>}
                    </td>
                    <td className="text-right font-mono text-secondary">
                      {formatIndianNumber(session.frames_written, 0)}
                    </td>
                    <td className={`text-right font-mono ${session.data_loss_pct >= 1 ? "text-danger" : session.data_loss_pct > 0 ? "text-warning" : "text-muted"}`}>
                      {formatPercent(session.data_loss_pct, 2)}
                    </td>
                    <td className={`text-right font-mono ${session.session_loss_pct >= 1 ? "text-danger" : session.session_loss_pct > 0 ? "text-warning" : "text-muted"}`}>
                      {formatPercent(session.session_loss_pct, 2)}
                    </td>
                    <td className={session.grid_seconds_lost ? "text-right text-danger" : "text-right text-muted"}>{session.grid_seconds_lost}</td>
                    <td className={session.grid_gaps ? "text-right text-danger" : "text-right text-muted"}>{session.grid_gaps}</td>
                    <td className={session.stale_seconds ? "text-right text-danger" : "text-right text-muted"}>{session.stale_seconds}</td>
                    <td className={session.dropped_batches ? "text-right text-danger" : "text-right text-muted"}>{session.dropped_batches}</td>
                    <td className={session.unmatched_ticks ? "text-right text-warning" : "text-right text-muted"}>{session.unmatched_ticks}</td>
                    <td className={session.reconnects ? "text-right text-warning" : "text-right text-muted"}>{session.reconnects}</td>
                    <td className="text-right font-mono text-secondary">
                      {session.uptime_ms > 0
                        ? formatIndianNumber(session.ticks_received / (session.uptime_ms / 1000), 1)
                        : "0.0"}
                    </td>
                    <td className="text-right font-mono text-secondary">{formatUptime(session.uptime_ms)}</td>
                    <td className="text-right font-mono text-secondary">{formatBytes(session.disk_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Panel>
  );
});
