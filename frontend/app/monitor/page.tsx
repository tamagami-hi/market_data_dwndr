"use client";

import { CompressionActivity } from "@/components/monitor/CompressionActivity";
import { DataLossDiagnostics } from "@/components/monitor/DataLossDiagnostics";
import { FrameIntegrity } from "@/components/monitor/FrameIntegrity";
import { MonitorAlerts } from "@/components/monitor/MonitorAlerts";
import { MonitorHeader } from "@/components/monitor/MonitorHeader";
import { MonitorKpis } from "@/components/monitor/MonitorKpis";
import { SessionHistory } from "@/components/monitor/SessionHistory";
import { SessionLogs } from "@/components/monitor/SessionLogs";
import { StorageHistory } from "@/components/monitor/StorageHistory";
import { UnderlyingHealth } from "@/components/monitor/UnderlyingHealth";
import { PageFrame } from "@/components/ui/PageFrame";
import { useMonitorTelemetry } from "@/hooks/useMonitorTelemetry";

export default function MonitorPage() {
  const telemetry = useMonitorTelemetry();

  return (
    // On a tall viewport the dashboard fills the screen and the three rows share the
    // spare height evenly (see auto-rows-fr below) — that is what gave v0.1.26 its
    // uniform rows. A min-height, not a fixed height with overflow-hidden, so content
    // can still grow past it and the page simply scrolls.
    <PageFrame className="[@media(min-height:900px)]:min-h-[calc(100dvh-7rem)]">
      <MonitorHeader
        globals={telemetry.live.globals}
        tradingDate={telemetry.context.tradingDate}
        shownDate={telemetry.context.shownDate}
        captureRunning={telemetry.context.captureRunning}
        isPastSession={telemetry.source.isPastSession}
        source={telemetry.source.type}
        refreshWindow={telemetry.context.refreshWindow}
        lastSuccessAt={telemetry.freshness.lastSuccessAt}
      />

      <MonitorKpis
        globals={telemetry.live.globals}
        expectedFrames={telemetry.context.expectedFrames}
      />

      <MonitorAlerts
        globals={telemetry.live.globals}
        restError={telemetry.freshness.restError}
        payloadError={telemetry.freshness.payloadError}
        isRestStale={telemetry.freshness.isRestStale}
      />

      {/* Dashboard grid — panel order restored from v0.1.26.
          ONE grid with the panels as DIRECT children, not two column stacks and not
          per-panel wrapper divs. Both of those let each panel keep its own content
          height, so nothing lines up across the gutter. As direct grid children,
          auto-placement puts each pair on a shared row and `.panel { height: 100% }`
          makes both fill it.

          Children are in ROW order (left, right, left, right, …), which is also the
          order the single-column mobile layout reads in:

              Data loss        | Per-underlying health
              Frame integrity  | Session history
              Download history | Compression

          Columns are 1fr / 1.25fr: the right carries the wide data tables, the left the
          denser stat surfaces. Session logs sits BELOW the grid as a full-width strip,
          as it did in v0.1.26 — it is a ticker, not a data surface, so pairing it into a
          row forced every other row taller to match. */}
      <div className="grid grid-cols-1 gap-3 [@media(min-height:900px)]:flex-1 lg:grid-cols-[1fr_1.25fr] [@media(min-height:900px)]:lg:auto-rows-fr">
        <DataLossDiagnostics globals={telemetry.live.globals} />
        <UnderlyingHealth rows={telemetry.live.rows} />

        <FrameIntegrity
          rows={telemetry.live.rows}
          globals={telemetry.live.globals}
          expectedFrames={telemetry.context.expectedFrames}
        />
        <SessionHistory sessions={telemetry.history.sessions} />

        <StorageHistory history={telemetry.history.capture} />
        <CompressionActivity
          current={telemetry.live.compression}
          history={telemetry.history.compression}
        />
      </div>

      <SessionLogs logs={telemetry.live.logs} />
    </PageFrame>
  );
}
