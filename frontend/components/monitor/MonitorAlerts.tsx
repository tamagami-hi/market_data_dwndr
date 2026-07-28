import { StateMessage } from "@/components/ui/StateMessage";
import type { GlobalStatus } from "@/lib/wsTypes";

export function MonitorAlerts({
  globals,
  restError,
  payloadError,
  isRestStale,
}: {
  globals: GlobalStatus | null;
  restError: string | null;
  payloadError: string | null;
  isRestStale: boolean;
}) {
  const alerts = [
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
            globals.data_age_ms === null ? "an unknown time" : `${(globals.data_age_ms / 1000).toFixed(1)} seconds`
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
    restError
      ? {
          title: "REST refresh failed",
          detail: `${restError} Last valid values remain on screen.`,
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
