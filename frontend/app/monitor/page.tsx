"use client";

import { useMemo } from "react";

import { CompressionActivity } from "@/components/monitor/CompressionActivity";
import { DataLossDiagnostics } from "@/components/monitor/DataLossDiagnostics";
import { FrameIntegrity } from "@/components/monitor/FrameIntegrity";
import { MonitorAlerts } from "@/components/monitor/MonitorAlerts";
import { MonitorHeader, RetainedSessionBanner } from "@/components/monitor/MonitorHeader";
import { MonitorKpis } from "@/components/monitor/MonitorKpis";
import { MonitorRestNotifications } from "@/components/monitor/MonitorRestNotifications";
import { SessionHistory } from "@/components/monitor/SessionHistory";
import { SessionLogs } from "@/components/monitor/SessionLogs";
import { StorageHistory } from "@/components/monitor/StorageHistory";
import { UnderlyingHealth } from "@/components/monitor/UnderlyingHealth";
import { useOperatorEvents } from "@/components/operator-events/OperatorEventsProvider";
import { PageFrame } from "@/components/ui/PageFrame";
import { useMonitorTelemetry } from "@/hooks/useMonitorTelemetry";
import { retainedSessionNotice } from "@/lib/monitor/retainedSession";
import { liveSessionSummary } from "@/lib/monitor/viewModel";

export default function MonitorPage() {
  const telemetry = useMonitorTelemetry();
  const { logs } = useOperatorEvents();

  // While capture runs, fold the in-progress session into the history panel so its
  // Frames column keeps counting the frames actually written to the .bin files.
  const liveSession = useMemo(
    () =>
      telemetry.context.captureRunning &&
      telemetry.live.globals !== null &&
      telemetry.context.tradingDate !== null
        ? liveSessionSummary(
            telemetry.context.tradingDate,
            telemetry.live.globals,
            telemetry.live.rows,
          )
        : null,
    [
      telemetry.context.captureRunning,
      telemetry.context.tradingDate,
      telemetry.live.globals,
      telemetry.live.rows,
    ],
  );

  // Non-null while the panels show a retained snapshot (e.g. Friday's session on a
  // Sunday) instead of live telemetry; every live-only figure is labelled from it.
  const retainedDate = telemetry.source.retainedDate ?? null;
  const notice = useMemo(
    () =>
      retainedSessionNotice({
        retainedDate,
        tradingDate: telemetry.context.tradingDate,
        marketPhase: telemetry.context.marketPhase ?? null,
        calendar: telemetry.context.calendarDay ?? null,
      }),
    [
      retainedDate,
      telemetry.context.tradingDate,
      telemetry.context.marketPhase,
      telemetry.context.calendarDay,
    ],
  );

  // Only show the backend's verdict next to the figures it describes: the snapshot's date
  // when retained, otherwise today's.
  const shownDay = retainedDate ?? telemetry.context.tradingDate;
  const candidateOutcome = telemetry.history.sessionOutcome ?? null;
  const sessionOutcome =
    candidateOutcome && candidateOutcome.trading_date === shownDay ? candidateOutcome : null;

  return (
    <PageFrame>
      <MonitorRestNotifications
        error={telemetry.freshness.restError}
        hasCompletedRefresh={telemetry.freshness.lastSuccessAt !== null}
      />

      <MonitorHeader
        globals={telemetry.live.globals}
        tradingDate={telemetry.context.tradingDate}
        marketPhase={telemetry.context.marketPhase ?? null}
        captureRunning={telemetry.context.captureRunning}
        notice={notice}
        refreshWindow={telemetry.context.refreshWindow}
        lastSuccessAt={telemetry.freshness.lastSuccessAt}
      />

      <RetainedSessionBanner notice={notice} />

      <MonitorKpis
        globals={telemetry.live.globals}
        expectedFrames={telemetry.context.expectedFrames}
        fpsHistory={telemetry.live.fpsHistory}
        series={telemetry.live.kpiSeries}
        isRetained={retainedDate !== null}
      />

      <MonitorAlerts
        globals={telemetry.live.globals}
        restError={telemetry.freshness.restError}
        payloadError={telemetry.freshness.payloadError}
        isRestStale={telemetry.freshness.isRestStale}
        lastSuccessAt={telemetry.freshness.lastSuccessAt}
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
              Download history | Session history
              Frame integrity  | Compression

          The two scrolling history tables share one tall row; Frame integrity and
          Compression are fixed-size summaries, so their row sizes to its content.

          Columns use a 45:50 ratio: the right carries the wide data tables, the left the
          denser stat surfaces. Session logs sits BELOW the grid as a full-width strip,
          as it did in v0.1.26 — it is a ticker, not a data surface, so pairing it into a
          row forced every other row taller to match. */}
      <div className="monitor-panel-grid grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,45fr)_minmax(0,50fr)]">
        <DataLossDiagnostics
          globals={telemetry.live.globals}
          retainedDate={retainedDate}
          sessionOutcome={sessionOutcome}
        />
        <UnderlyingHealth rows={telemetry.live.rows} retainedDate={retainedDate} />

        <StorageHistory history={telemetry.history.capture} />
        <SessionHistory sessions={telemetry.history.sessions} liveSession={liveSession} />

        <FrameIntegrity
          rows={telemetry.live.rows}
          globals={telemetry.live.globals}
          expectedFrames={telemetry.context.expectedFrames}
          retainedDate={retainedDate}
        />
        <CompressionActivity
          current={telemetry.live.compression}
          history={telemetry.history.compression}
        />
      </div>

      <SessionLogs logs={logs} />
    </PageFrame>
  );
}
