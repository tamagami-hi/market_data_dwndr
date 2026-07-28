import ConnectionDot from "@/components/ConnectionDot";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import type { RefreshWindow } from "@/lib/api";
import { formatClockTime, formatUptime } from "@/lib/numberFormat";
import { captureSeverity } from "@/lib/monitor/severity";
import { captureStatusConnection, sessionConnection } from "@/lib/wsTopicConnection";
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
    <PageHeader
      title="Capture Monitor"
      description={
        isPastSession && shownDate
          ? `Showing the retained ${shownDate} session.`
          : "Live capture health, data-loss accounting, storage, and session diagnostics."
      }
      actions={
        <>
          <div className="text-right text-xs text-muted">
            <div>
              {tradingDate ?? "No trading date"} / uptime {formatUptime(globals?.uptime_ms)}
            </div>
            <div>
              {lastSuccessAt ? `refreshed ${formatClockTime(lastSuccessAt)}` : "refresh pending"}
              {refreshWindow?.local_time ? ` / server ${refreshWindow.local_time}` : ""}
            </div>
          </div>
          <StatusIndicator label={stateLabel} severity={severity} />
          <ConnectionDot connection={captureStatusConnection} label="capture" />
          <ConnectionDot connection={sessionConnection} label="session" showLatency={false} />
        </>
      }
    />
  );
}
