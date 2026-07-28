import type { AuthStatus } from "@/lib/api";
import type { Severity } from "@/lib/monitor/severity";

export type StageState = "pending" | "active" | "complete" | "error";

export interface InitializationStage {
  label: string;
  detail: string;
  state: StageState;
}

export interface DownloaderInitialization {
  progress: number;
  headline: string;
  tone: Severity;
  stages: InitializationStage[];
}

function baseStages(states: StageState[]): InitializationStage[] {
  const definitions = [
    ["Backend configuration", "Environment and storage paths loaded."],
    ["Secure token broker", "HTTPS token source and passcode configured."],
    ["Token fetch and validation", "Waiting for a validated daily Kite session."],
    ["Downloader", "Waiting for capture prerequisites and market hours."],
  ] as const;
  return definitions.map(([label, detail], index) => ({ label, detail, state: states[index] }));
}

function updateStage(
  stages: InitializationStage[],
  index: number,
  detail: string,
): InitializationStage[] {
  return stages.map((stage, stageIndex) => stageIndex === index ? { ...stage, detail } : stage);
}

export function downloaderInitialization(
  status: AuthStatus | null | undefined,
): DownloaderInitialization {
  if (status === undefined) {
    return {
      progress: 8,
      headline: "Connecting to the downloader service",
      tone: "neutral",
      stages: baseStages(["active", "pending", "pending", "pending"]),
    };
  }
  if (status === null) {
    return {
      progress: 0,
      headline: "Backend is unreachable",
      tone: "danger",
      stages: baseStages(["error", "pending", "pending", "pending"]),
    };
  }
  if (!status.configured) {
    return {
      progress: 15,
      headline: "Backend environment is incomplete",
      tone: "danger",
      stages: baseStages(["error", "pending", "pending", "pending"]),
    };
  }
  if (!status.external_token_source_configured && !status.authenticated) {
    const stages = updateStage(
      baseStages(["complete", "error", "pending", "pending"]),
      1,
      "Configure KITE_TOKEN_BROKER_URL and its passcode in backend/.env.",
    );
    return {
      progress: 25,
      headline: "Secure token broker is not configured",
      tone: "danger",
      stages,
    };
  }
  if (!status.authenticated) {
    const attempted = Boolean(status.automation?.last_broker_poll_at);
    const detail = status.automation?.last_error
      ? status.automation.last_error
      : attempted
        ? "Token received or pending. The backend validates it with Kite before saving it."
        : "Waiting for the configured trading-day token polling window.";
    return {
      progress: attempted ? 55 : 40,
      headline: attempted ? "Fetching and validating the daily token" : "Waiting to fetch the daily token",
      tone: status.automation?.last_error ? "warning" : "neutral",
      stages: updateStage(
        baseStages(["complete", "complete", attempted ? "active" : "pending", "pending"]),
        2,
        detail,
      ),
    };
  }
  if (!status.capture_ready) {
    const brokerState = status.external_token_source_configured ? "complete" : "pending";
    const brokerDetail = status.external_token_source_configured
      ? "HTTPS token source and passcode configured."
      : "Not configured. This validated session came from the retained fallback flow.";
    const withBroker = updateStage(
      baseStages(["complete", brokerState, "complete", "active"]),
      1,
      brokerDetail,
    );
    const withToken = updateStage(withBroker, 2, "Kite accepted the token and saved the daily session.");
    return {
      progress: 75,
      headline: "Token validated; capture prerequisites pending",
      tone: "warning",
      stages: updateStage(withToken, 3, "Waiting for the daily risk-free rate before capture can initialize."),
    };
  }

  const running = Boolean(status.capture?.running);
  const brokerState = status.external_token_source_configured ? "complete" : "pending";
  const stages = baseStages(["complete", brokerState, "complete", "complete"]);
  const brokerDetail = status.external_token_source_configured
    ? "HTTPS token source and passcode configured."
    : "Not configured. This validated session came from the retained fallback flow.";
  const captureDetail = running
    ? `Capturing ${status.capture?.tokens ?? 0} subscribed instruments for ${status.capture?.trading_date ?? "today"}.`
    : "Downloader is ready. The scheduler is waiting for the configured market window.";
  return {
    progress: 100,
    headline: running ? "Downloader is running" : "Downloader initialized",
    tone: "success",
    stages: updateStage(updateStage(stages, 1, brokerDetail), 3, captureDetail),
  };
}
