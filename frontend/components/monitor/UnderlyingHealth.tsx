import { Panel } from "@/components/ui/Panel";
import { ResponsiveDisclosure } from "@/components/ui/ResponsiveDisclosure";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { StateMessage } from "@/components/ui/StateMessage";
import { formatBytes, formatClockTime, formatIndianNumber, formatPercent } from "@/lib/numberFormat";
import { lossSeverity } from "@/lib/monitor/severity";
import type { PerUnderlyingStatus } from "@/lib/wsTypes";

function StreamDetails({ row }: { row: PerUnderlyingStatus }) {
  const details = [
    ["Full-session loss", formatPercent(row.frame_loss_pct, 2)],
    ["Day complete", formatPercent(row.day_complete_pct ?? 100 - row.frame_loss_pct, 1)],
    ["Elapsed expected", formatIndianNumber(row.session_frames_expected ?? 0, 0)],
    ["Bytes / frame", formatBytes(row.avg_bytes_per_frame)],
    ["Projected EOD", formatBytes(row.projected_eod_bytes)],
    ["File size", formatBytes(row.file_bytes)],
    ["Last tick", formatClockTime(row.last_tick_ms)],
    ["Heartbeat age", row.heartbeat_age_ms === null ? "--" : `${(row.heartbeat_age_ms / 1000).toFixed(1)}s`],
    ["Applied ticks", formatIndianNumber(row.applied ?? 0, 0)],
    ["Unmatched ticks", formatIndianNumber(row.unmatched, 0)],
    ["Writer pending", formatIndianNumber(row.writer_pending ?? 0, 0)],
    ["Data freshness", row.data_fresh ? "fresh" : "frozen"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
      {details.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted">{label}</dt>
          <dd className="mt-0.5 font-mono text-primary">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CompactColumnHeader({
  label,
  shortLabel,
  className = "text-right",
}: {
  label: string;
  shortLabel: string;
  className?: string;
}) {
  return (
    <th className={className}>
      <span aria-hidden="true">{shortLabel}</span>
      <span className="sr-only">{label}</span>
    </th>
  );
}

function StreamConnectionIndicator({
  connected,
  underlying,
}: {
  connected: boolean;
  underlying: string;
}) {
  return (
    <span
      aria-label={`${underlying} connection: ${connected ? "connected" : "offline"}`}
      className={`inline-block size-2.5 rounded-full ${
        connected ? "bg-success" : "border-2 border-danger bg-transparent"
      }`}
      role="img"
    />
  );
}

export function UnderlyingHealth({ rows }: { rows: PerUnderlyingStatus[] }) {
  return (
    <Panel title="Per-underlying health" subtitle={`${rows.length} streams`} className="h-full">
      {rows.length === 0 ? (
        <div className="p-3">
          <StateMessage title="Waiting for capture telemetry">
            The market-hours scheduler has not published stream health yet.
          </StateMessage>
        </div>
      ) : (
        <>
          <div className="space-y-2 p-2 xl:hidden">
            {rows.map((row) => (
              <ResponsiveDisclosure
                key={row.underlying}
                id={`stream-${row.underlying}`}
                label={`${row.underlying} stream details`}
                summary={
                  <div className="grid grid-cols-[1fr_auto] items-center gap-2 text-xs">
                    <div>
                      <div className="font-semibold text-primary">{row.underlying}</div>
                      <div className="mt-1 text-muted">
                        {formatIndianNumber(row.frames_written, 0)} frames /{" "}
                        <span className={lossSeverity(row.session_loss_pct) === "neutral" ? "" : "text-warning"}>
                          {formatPercent(row.session_loss_pct ?? 0, 2)} loss
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <StatusIndicator
                        compact
                        label={row.connected ? "connected" : "offline"}
                        severity={row.connected ? "success" : "danger"}
                      />
                      <span className={row.heartbeat_ok ? "text-success" : "text-danger"}>
                        {row.heartbeat_ok ? "1 Hz heartbeat" : "stale heartbeat"}
                      </span>
                    </div>
                  </div>
                }
              >
                <StreamDetails row={row} />
              </ResponsiveDisclosure>
            ))}
          </div>
          <div className="hidden min-h-0 flex-1 overflow-x-hidden overflow-y-auto xl:block">
            <table className="data-table monitor-health-table table-fixed font-mono tabular-nums">
              <colgroup>
                <col className="w-[13%]" />
                <col className="w-[6%]" />
                <col className="w-[9%]" />
                <col className="w-[9%]" />
                <col className="w-[9%]" />
                <col className="w-[10%]" />
                <col className="w-[11%]" />
                <col className="w-[10%]" />
                <col className="w-[13%]" />
                <col className="w-[10%]" />
              </colgroup>
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="sticky left-0 z-20 text-left">Stream</th>
                  <CompactColumnHeader
                    className="text-center"
                    label="Connection"
                    shortLabel="Link"
                  />
                  <th className="text-right">Frames</th>
                  <CompactColumnHeader label="Elapsed loss" shortLabel="Loss" />
                  <CompactColumnHeader label="Day progress" shortLabel="Day" />
                  <CompactColumnHeader label="Bytes per frame" shortLabel="B/frame" />
                  <CompactColumnHeader label="Projected EOD" shortLabel="EOD" />
                  <th className="text-right">File</th>
                  <CompactColumnHeader label="Last tick" shortLabel="Tick" />
                  <CompactColumnHeader label="Heartbeat" shortLabel="HB" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.underlying}>
                    <td className="sticky left-0 z-10 bg-surface-1 font-sans font-semibold text-primary">
                      {row.underlying}
                    </td>
                    <td className="text-center">
                      <StreamConnectionIndicator
                        connected={row.connected}
                        underlying={row.underlying}
                      />
                    </td>
                    <td className="text-right text-primary">{formatIndianNumber(row.frames_written, 0)}</td>
                    <td className={`text-right ${lossSeverity(row.session_loss_pct) === "neutral" ? "text-secondary" : "text-warning"}`}>
                      {formatPercent(row.session_loss_pct ?? 0, 2)}
                    </td>
                    <td className="text-right text-secondary">
                      {formatPercent(row.day_complete_pct ?? 100 - row.frame_loss_pct, 1)}
                    </td>
                    <td className="text-right text-secondary">{formatBytes(row.avg_bytes_per_frame)}</td>
                    <td className="text-right text-secondary">{formatBytes(row.projected_eod_bytes)}</td>
                    <td className="text-right text-secondary">{formatBytes(row.file_bytes)}</td>
                    <td className="text-right text-secondary">{formatClockTime(row.last_tick_ms)}</td>
                    <td className={`text-right ${row.heartbeat_ok ? "text-success" : "text-danger"}`}>
                      {row.heartbeat_ok ? "1 Hz" : "stale"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Panel>
  );
}
