import { StateMessage } from "@/components/ui/StateMessage";
import type { GlobalStatus } from "@/lib/wsTypes";
import { formatClockTime, formatDuration } from "@/lib/numberFormat";

/**
 * The backend cannot be reached: polls are failing and the data on screen is no longer
 * being refreshed.
 *
 * A single failed poll is only logged (it usually recovers on the next one). The banner
 * appears once the failure has outlived the stale threshold, or immediately when nothing
 * has ever loaded. It used to be suppressed in exactly this case — the stale banner was
 * hidden whenever a REST error was set, so an unreachable backend showed no alert at all.
 */
function unreachableAlert(
  restError: string | null,
  isRestStale: boolean,
  lastSuccessAt: number | null,
) {
  if (!restError || (!isRestStale && lastSuccessAt !== null)) return null;
  return {
    title:
      lastSuccessAt === null
        ? "Backend unreachable — no update received yet"
        : `Backend unreachable — last update ${formatClockTime(lastSuccessAt)}`,
    detail: `${restError} Everything below is from the last successful refresh and is not current.`,
    severity: "danger" as const,
  };
}

export function MonitorAlerts({
  globals,
  restError,
  payloadError,
  isRestStale,
  lastSuccessAt,
}: {
  globals: GlobalStatus | null;
  restError: string | null;
  payloadError: string | null;
  isRestStale: boolean;
  /** When the last REST refresh succeeded; null when none has. */
  lastSuccessAt: number | null;
}) {
  const alerts = [
    unreachableAlert(restError, isRestStale, lastSuccessAt),
    globals?.exhausted
      ? {
          title: "Recovery exhausted",
          detail: "Automatic ticker recovery stopped. Restart the capture service after checking credentials and uplink health.",
          severity: "danger" as const,
        }
      : null,
    globals?.stale
      ? {
          title: "Live feed is stale",
          detail: `The last data change was ${
            globals.data_age_ms === null ? "an unknown time" : formatDuration(globals.data_age_ms)
          } ago. Retained values remain visible while recovery runs.`,
          severity: "danger" as const,
        }
      : null,
    !globals?.stale && (globals?.degraded || globals?.ingestion_degraded)
      ? {
          title: "Capture is degraded",
          detail: "The writer or ticker is recovering. Review per-stream health and data-loss diagnostics.",
          severity: "warning" as const,
        }
      : null,
    payloadError
      ? { title: "Malformed telemetry rejected", detail: payloadError, severity: "warning" as const }
      : null,
    isRestStale && !restError
      ? {
          title: "Historical data is stale",
          detail: "No recent REST refresh has completed. Live WebSocket telemetry may still be current.",
          severity: "warning" as const,
        }
      : null,
  ].filter((alert) => alert !== null);

  if (alerts.length === 0) return null;
  return (
    <section aria-label="Operational alerts" className="grid gap-2 lg:grid-cols-2">
      {alerts.map((alert, index) => {
        const isUnpairedFinalAlert = alerts.length % 2 === 1 && index === alerts.length - 1;
        return (
          <div key={alert.title} className={isUnpairedFinalAlert ? "lg:col-span-2" : ""}>
            <StateMessage title={alert.title} severity={alert.severity} role="alert" className="h-full">
              {alert.detail}
            </StateMessage>
          </div>
        );
      })}
    </section>
  );
}
