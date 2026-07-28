import { Metric } from "@/components/ui/Metric";
import { formatBytes, formatIndianNumber, formatPercent } from "@/lib/numberFormat";
import { fpsSeverity, lossSeverity } from "@/lib/monitor/severity";
import type { GlobalStatus } from "@/lib/wsTypes";

export function MonitorKpis({
  globals,
  expectedFrames,
}: {
  globals: GlobalStatus | null;
  expectedFrames: number;
}) {
  const diskPercent =
    globals && globals.disk_total_bytes > 0
      ? ((globals.disk_total_bytes - globals.disk_free_bytes) / globals.disk_total_bytes) * 100
      : 0;
  return (
    <section aria-label="Primary capture metrics" className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
      <Metric
        compact
        label="Tokens"
        value={globals ? formatIndianNumber(globals.tokens, 0) : "--"}
        detail="subscribed instruments"
      />
      <Metric
        compact
        label="Frames / sec"
        value={globals ? globals.fps.toFixed(2) : "--"}
        severity={fpsSeverity(globals?.fps)}
        detail={globals ? `${globals.writer_lag_max} writer lag / ${globals.snapshot_ms.toFixed(1)}ms build` : undefined}
      />
      <Metric
        compact
        label="Captures"
        value={globals ? formatIndianNumber(globals.captures, 0) : "--"}
        detail="grid snapshots written"
      />
      <Metric
        compact
        label="Drop rate"
        value={globals ? formatPercent(globals.drop_rate_pct, 3) : "--"}
        severity={globals?.drop_rate_pct ? "danger" : "success"}
        detail={globals ? `${formatIndianNumber(globals.dropped_batches, 0)} batches` : undefined}
      />
      <Metric
        compact
        label="Disk used"
        value={globals ? formatBytes(globals.disk_bytes) : "--"}
        severity={diskPercent >= 90 ? "danger" : diskPercent >= 75 ? "warning" : "neutral"}
        detail={globals ? `${formatBytes(globals.disk_free_bytes)} free / ${diskPercent.toFixed(0)}% used` : undefined}
      />
      <Metric
        compact
        label="Frame loss"
        value={globals ? formatPercent(globals.frame_loss_pct, 2) : "--"}
        severity={lossSeverity(globals?.session_loss_pct)}
        detail={
          globals
            ? `${formatIndianNumber(globals.frames_written, 0)} / ${formatIndianNumber(
                globals.frames_expected || expectedFrames,
                0,
              )} full-session frames`
            : undefined
        }
      />
    </section>
  );
}
