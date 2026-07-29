import { Metric } from "@/components/ui/Metric";
import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import { formatIndianNumber, formatPercent } from "@/lib/numberFormat";
import { lossSeverity } from "@/lib/monitor/severity";
import type { GlobalStatus } from "@/lib/wsTypes";

export function DataLossDiagnostics({ globals }: { globals: GlobalStatus | null }) {
  const items = globals
    ? [
        {
          label: "Seconds lost",
          value: formatIndianNumber(globals.grid_seconds_lost ?? 0, 0),
          severity: globals.grid_seconds_lost ? "danger" as const : "success" as const,
        },
        {
          label: "Gap events",
          value: formatIndianNumber(globals.grid_gaps ?? 0, 0),
          severity: globals.grid_gaps ? "danger" as const : "success" as const,
        },
        {
          label: "Frozen seconds",
          value: formatIndianNumber(globals.frozen_seconds ?? 0, 0),
          severity: globals.frozen_seconds ? "warning" as const : "success" as const,
        },
        {
          label: "Elapsed loss",
          value: formatPercent(globals.session_loss_pct ?? 0, 3),
          severity: lossSeverity(globals.session_loss_pct),
        },
        {
          label: "Unmatched ticks",
          value: formatIndianNumber(globals.unmatched_ticks ?? 0, 0),
          severity: globals.unmatched_ticks ? "warning" as const : "neutral" as const,
        },
        {
          label: "Writer lag",
          value: formatIndianNumber(globals.writer_lag_max, 0),
          severity: globals.writer_lag_max ? "warning" as const : "neutral" as const,
        },
        {
          label: "Ticks / sec",
          value: formatIndianNumber(globals.ticks_per_sec ?? 0, 1),
          severity: "neutral" as const,
        },
        {
          label: "Disk runway",
          value: globals.disk_runway_hours ? `${formatIndianNumber(globals.disk_runway_hours, 1)} h` : "--",
          severity: globals.disk_runway_hours && globals.disk_runway_hours < 8 ? "warning" as const : "neutral" as const,
        },
        {
          label: "Reconnects",
          value: formatIndianNumber(globals.reconnects ?? 0, 0),
          severity: globals.exhausted
            ? "danger" as const
            : globals.reconnects
              ? "warning" as const
              : "neutral" as const,
        },
      ]
    : [];

  return (
    <Panel title="Data-loss diagnostics" subtitle="current session" className="h-full">
      {items.length > 0 ? (
        <div className="grid grid-cols-3 gap-2 p-2">
          {items.map((item) => <Metric key={item.label} compact {...item} />)}
        </div>
      ) : (
        <div className="p-3">
          <StateMessage title="Awaiting data-loss telemetry">
            Loss, gaps, frozen intervals, and writer lag appear with capture status.
          </StateMessage>
        </div>
      )}
    </Panel>
  );
}
