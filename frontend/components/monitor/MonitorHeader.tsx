import { MonitorConnections } from "@/components/monitor/MonitorConnections";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import type { RefreshWindow } from "@/lib/api";
import { formatClockTime, formatUptime } from "@/lib/numberFormat";
import { captureSeverity } from "@/lib/monitor/severity";
import type { GlobalStatus } from "@/lib/wsTypes";

export function MonitorHeader({
  globals,
  tradingDate,
  shownDate,
  captureRunning,
  isPastSession,
  source,
  refreshWindow,
  lastSuccessAt,
}: {
  globals: GlobalStatus | null;
  tradingDate: string | null;
  shownDate: string | null;
  captureRunning: boolean;
  isPastSession: boolean;
  source: "none" | "live" | "persisted";
  refreshWindow: RefreshWindow | null;
  lastSuccessAt: number | null;
}) {
  const severity = globals
    ? captureSeverity({
        exhausted: Boolean(globals.exhausted),
        stale: globals.stale,
        degraded: globals.degraded || globals.ingestion_degraded,
      })
    : "neutral";
  const stateLabel = globals?.exhausted
    ? "Recovery exhausted"
    : globals?.stale
      ? "Feed stale"
      : globals?.degraded || globals?.ingestion_degraded
        ? "Degraded"
        : captureRunning
          ? "Capturing"
          : source === "persisted"
            ? "Retained"
            : "Idle";

  return (
    <header className="monitor-header">
      <h1 className="shrink-0 whitespace-nowrap text-lg font-semibold tracking-tight text-primary sm:text-xl">
        Capture Monitor
      </h1>
      <p className="sr-only">
        {
        isPastSession && shownDate
          ? `Showing the retained ${shownDate} session.`
          : "Live capture health, data-loss accounting, storage, and session diagnostics."
        }
      </p>
      <div className="monitor-context-scroll">
        <div className="monitor-context-line" aria-label="Monitor session context">
          <span>{tradingDate ?? "No trading date"} / uptime {formatUptime(globals?.uptime_ms)}</span>
          <span aria-hidden="true">/</span>
          <span>
            {lastSuccessAt ? `refreshed ${formatClockTime(lastSuccessAt)}` : "refresh pending"}
            {refreshWindow?.local_time ? ` / server ${refreshWindow.local_time}` : ""}
          </span>
          <StatusIndicator label={stateLabel} severity={severity} />
          <MonitorConnections />
        </div>
      </div>
    </header>
  );
}
