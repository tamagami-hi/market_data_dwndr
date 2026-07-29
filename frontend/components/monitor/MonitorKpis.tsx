import { Metric } from "@/components/ui/Metric";
import { MiniGauge, Sparkline } from "@/components/ui/Sparkline";
import { formatBytes, formatIndianNumber, formatPercent } from "@/lib/numberFormat";
import { fpsSeverity, lossSeverity } from "@/lib/monitor/severity";
import type { KpiSeries } from "@/hooks/useMonitorTelemetry";
import type { GlobalStatus } from "@/lib/wsTypes";

/**
 * The six primary metrics, each with a visual in its `accessory` slot.
 *
 * The visual sits on the value's row, so the cards keep their existing height. Shape is
 * chosen per metric: a sparkline where the trend carries meaning, a gauge where the
 * number is a fraction of a known whole (see components/ui/Sparkline.tsx).
 */
export function MonitorKpis({
  globals,
  expectedFrames,
  fpsHistory = [],
  series,
}: {
  globals: GlobalStatus | null;
  expectedFrames: number;
  fpsHistory?: number[];
  series?: KpiSeries;
}) {
  const diskPercent =
    globals && globals.disk_total_bytes > 0
      ? ((globals.disk_total_bytes - globals.disk_free_bytes) / globals.disk_total_bytes) * 100
      : 0;
  const baseline = globals?.frames_expected || expectedFrames;
  const capturePercent = baseline > 0 ? ((globals?.captures ?? 0) / baseline) * 100 : 0;
  const completeness = globals ? Math.max(0, 100 - globals.frame_loss_pct) : 0;
  return (
    <section aria-label="Primary capture metrics" className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
      <Metric
        compact
        label="Tokens"
        value={globals ? formatIndianNumber(globals.tokens, 0) : "--"}
        detail="subscribed instruments"
        // Subscription count should be flat; a step down means instruments were dropped.
        accessory={<Sparkline values={series?.tokens ?? []} tone="neutral" />}
      />
      <Metric
        compact
        label="Frames / sec"
        value={globals ? globals.fps.toFixed(2) : "--"}
        severity={fpsSeverity(globals?.fps)}
        detail={globals ? `${globals.writer_lag_max} writer lag / ${globals.snapshot_ms.toFixed(1)}ms build` : undefined}
        // The 1 Hz grid: a flat line is healthy, dips are missed seconds.
        accessory={<Sparkline values={fpsHistory} tone={fpsSeverity(globals?.fps)} />}
      />
      <Metric
        compact
        label="Captures"
        value={globals ? formatIndianNumber(globals.captures, 0) : "--"}
        detail="grid snapshots written"
        // Cumulative, so a trend line would be a straight ramp; progress through the
        // session baseline is the useful reading.
        accessory={<MiniGauge percent={capturePercent} tone="neutral" title={`${capturePercent.toFixed(0)}% of the session baseline`} />}
      />
      <Metric
        compact
        label="Drop rate"
        value={globals ? formatPercent(globals.drop_rate_pct, 3) : "--"}
        severity={globals?.drop_rate_pct ? "danger" : "success"}
        detail={globals ? `${formatIndianNumber(globals.dropped_batches, 0)} batches` : undefined}
        accessory={
          <Sparkline
            values={series?.drop ?? []}
            tone={globals?.drop_rate_pct ? "danger" : "success"}
          />
        }
      />
      <Metric
        compact
        label="Disk used"
        value={globals ? formatBytes(globals.disk_bytes) : "--"}
        severity={diskPercent >= 90 ? "danger" : diskPercent >= 75 ? "warning" : "neutral"}
        detail={globals ? `${formatBytes(globals.disk_free_bytes)} free / ${diskPercent.toFixed(0)}% used` : undefined}
        // Fraction of the volume — a gauge answers "how close to full".
        accessory={
          <MiniGauge
            percent={diskPercent}
            tone={diskPercent >= 90 ? "danger" : diskPercent >= 75 ? "warning" : "success"}
            title={`${diskPercent.toFixed(0)}% of the volume used`}
          />
        }
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
        // Gauge shows completeness (100 - loss): near-full is healthy.
        accessory={
          <MiniGauge
            percent={completeness}
            tone={completeness >= 99 ? "success" : completeness >= 95 ? "warning" : "danger"}
            title={`${completeness.toFixed(2)}% of full-session frames captured`}
          />
        }
      />
    </section>
  );
}
