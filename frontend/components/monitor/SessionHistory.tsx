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
    ["Dropped batches", formatIndianNumber(session.dropped_batches, 0)],
    ["Drop rate", formatPercent(session.drop_rate_pct, 3)],
    ["Unmatched ticks", formatIndianNumber(session.unmatched_ticks, 0)],
    ["Average ticks / sec", formatIndianNumber(averageTicks, 0)],
    ["Reconnects", formatIndianNumber(session.reconnects, 0)],
    ["Token refreshes", formatIndianNumber(session.token_refreshes, 0)],
    ["Disk", formatBytes(session.disk_bytes)],
    ["State", session.exhausted ? "recovery exhausted" : "complete"],
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

export const SessionHistory = memo(function SessionHistory({
  sessions,
}: {
  sessions: SessionSummary[];
}) {
  return (
    <Panel title="Session history" subtitle={`${sessions.length} recorded sessions`}>
      {sessions.length === 0 ? (
        <div className="p-3">
          <StateMessage title="No completed sessions">A session record appears after capture finalization.</StateMessage>
        </div>
      ) : (
        <>
          <div className="space-y-2 p-2 md:hidden">
            {sessions.map((session) => (
              <ResponsiveDisclosure
                key={session.trading_date}
                id={`session-${session.trading_date}`}
                label={`${session.trading_date} session details`}
                summary={
                  <div className="text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-primary">{session.trading_date}</span>
                      <span className={session.exhausted ? "text-danger" : "text-secondary"}>
                        {formatUptime(session.uptime_ms)}
                      </span>
                    </div>
                    <div className="mt-1 grid grid-cols-4 gap-2 text-muted">
                      <span>{formatPercent(session.session_loss_pct, 2)} loss</span>
                      <span>{session.grid_seconds_lost} lost s</span>
                      <span>{session.grid_gaps} gaps</span>
                      <span>{session.frozen_seconds} frozen</span>
                    </div>
                  </div>
                }
              >
                <SessionDetails session={session} />
              </ResponsiveDisclosure>
            ))}
          </div>
          <div className="hidden min-h-0 flex-1 overflow-auto md:block">
            <table className="data-table tabular-nums">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="text-left">Session</th>
                  <th className="text-right">Frames</th>
                  <th className="text-right">Loss</th>
                  <th className="text-right">Lost s</th>
                  <th className="text-right">Gaps</th>
                  <th className="text-right">Frozen</th>
                  <th className="text-right">Dropped</th>
                  <th className="text-right">Unmatched</th>
                  <th className="text-right">Reconnects</th>
                  <th className="text-right">Ticks/s</th>
                  <th className="text-right">Uptime</th>
                  <th className="text-right">Disk</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => (
                  <tr key={session.trading_date}>
                    <td className="font-semibold text-primary">
                      {session.trading_date}
                      {session.exhausted && <span className="ml-2 text-xs text-danger">stalled</span>}
                    </td>
                    <td className="text-right font-mono text-secondary">
                      {formatIndianNumber(session.captures, 0)}
                    </td>
                    <td className={`text-right font-mono ${session.session_loss_pct >= 1 ? "text-danger" : session.session_loss_pct > 0 ? "text-warning" : "text-muted"}`}>
                      {formatPercent(session.session_loss_pct, 2)}
                    </td>
                    <td className={session.grid_seconds_lost ? "text-right text-danger" : "text-right text-muted"}>{session.grid_seconds_lost}</td>
                    <td className={session.grid_gaps ? "text-right text-danger" : "text-right text-muted"}>{session.grid_gaps}</td>
                    <td className={session.frozen_seconds ? "text-right text-warning" : "text-right text-muted"}>{session.frozen_seconds}</td>
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
