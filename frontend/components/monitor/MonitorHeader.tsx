import { MonitorConnections } from "@/components/monitor/MonitorConnections";
import { StateMessage } from "@/components/ui/StateMessage";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import type { RefreshWindow } from "@/lib/api";
import type { RetainedNotice } from "@/lib/monitor/retainedSession";
import { formatClockTime, formatUptime } from "@/lib/numberFormat";
import { captureSeverity, type Severity } from "@/lib/monitor/severity";
import type { GlobalStatus } from "@/lib/wsTypes";

function liveState(globals: GlobalStatus | null, captureRunning: boolean): {
  label: string;
  severity: Severity;
} {
  if (!globals) return { label: captureRunning ? "Capturing" : "Idle", severity: "neutral" };
  const severity = captureSeverity({
    exhausted: Boolean(globals.exhausted),
    stale: globals.stale,
    degraded: globals.degraded || globals.ingestion_degraded,
  });
  if (globals.exhausted) return { label: "Recovery exhausted", severity };
  if (globals.stale) return { label: "Feed stale", severity };
  if (globals.degraded || globals.ingestion_degraded) return { label: "Degraded", severity };
  return { label: captureRunning ? "Capturing" : "Idle", severity };
}

/**
 * Header state for a retained snapshot. It describes TODAY (is capture owed and missing?),
 * never the retained session's own health: a green dot over Friday's figures on a Sunday
 * read as "capturing fine right now".
 */
function retainedState(notice: RetainedNotice): { label: string; severity: Severity } {
  return notice.severity === "danger"
    ? { label: "Not capturing", severity: "danger" }
    : { label: "Retained", severity: notice.severity };
}

export function MonitorHeader({
  globals,
  tradingDate,
  marketPhase = null,
  captureRunning,
  notice = null,
  refreshWindow,
  lastSuccessAt,
}: {
  globals: GlobalStatus | null;
  tradingDate: string | null;
  /** Today's calendar phase (HOLIDAY / PRE_OPEN / OPEN / CLOSED). */
  marketPhase?: string | null;
  captureRunning: boolean;
  /** Set when the figures on screen are a retained snapshot, not live telemetry. */
  notice?: RetainedNotice | null;
  refreshWindow: RefreshWindow | null;
  lastSuccessAt: number | null;
}) {
  const state = notice ? retainedState(notice) : liveState(globals, captureRunning);
  const span = formatUptime(globals?.uptime_ms);

  return (
    <header className="monitor-header">
      <h1 className="shrink-0 whitespace-nowrap text-lg font-semibold tracking-tight text-primary sm:text-xl">
        Capture Monitor
      </h1>
      <p className="sr-only">
        {notice
          ? notice.title
          : "Live capture health, data-loss accounting, storage, and session diagnostics."}
      </p>
      <div className="monitor-context-scroll">
        <div className="monitor-context-line" aria-label="Monitor session context">
          <span>
            {tradingDate ?? "No trading date"}
            {marketPhase ? ` · ${marketPhase}` : ""}
            {" / "}
            {notice ? `last session span ${span}` : `uptime ${span}`}
          </span>
          <span aria-hidden="true">/</span>
          <span>
            {lastSuccessAt ? `refreshed ${formatClockTime(lastSuccessAt)}` : "refresh pending"}
            {refreshWindow?.local_time ? ` / server ${refreshWindow.local_time}` : ""}
          </span>
          <StatusIndicator label={state.label} severity={state.severity} />
          <MonitorConnections />
        </div>
      </div>
    </header>
  );
}

/** The visible "this is not live" banner, shown under the header for a retained snapshot. */
export function RetainedSessionBanner({ notice }: { notice: RetainedNotice | null }) {
  if (!notice) return null;
  return (
    <StateMessage
      title={notice.title}
      severity={notice.severity}
      role={notice.severity === "danger" ? "alert" : "status"}
    >
      {notice.detail}
    </StateMessage>
  );
}
