"use client";

import { useEffect, useState } from "react";

import { Metric } from "@/components/ui/Metric";
import { PageFrame } from "@/components/ui/PageFrame";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { createPollController } from "@/hooks/polling";
import { getAuthStatus, type AuthStatus } from "@/lib/api";
import { automationMessage } from "@/lib/automationStatus";
import {
  downloaderInitialization,
  type InitializationStage,
  type StageState,
} from "@/lib/downloader/initialization";
import type { Severity } from "@/lib/monitor/severity";

const STAGE_SEVERITY: Record<StageState, Severity> = {
  pending: "neutral",
  active: "warning",
  complete: "success",
  error: "danger",
};

export default function DownloaderPage() {
  const [status, setStatus] = useState<AuthStatus | null | undefined>(undefined);

  useEffect(() => {
    let isMounted = true;
    const controller = createPollController({
      intervalMs: () => 5_000,
      isPaused: () => document.hidden,
      task: async (signal) => {
        try {
          const next = await getAuthStatus(signal);
          if (isMounted) setStatus(next);
        } catch (error) {
          if (isMounted && !(error instanceof DOMException && error.name === "AbortError")) {
            setStatus(null);
          }
        }
      },
    });
    const handleVisibility = () => {
      if (!document.hidden) controller.resume();
    };
    controller.start();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      isMounted = false;
      document.removeEventListener("visibilitychange", handleVisibility);
      controller.stop();
    };
  }, []);

  const initialization = downloaderInitialization(status);
  return (
    <PageFrame>
      <PageHeader
        title="Downloader"
        eyebrow="Unattended VPS service"
        description="Daily token validation, capture prerequisites, market-window automation, and end-of-day compression."
        actions={<StatusIndicator label={initialization.headline} severity={initialization.tone} />}
      />

      <div className="mx-auto w-full max-w-5xl space-y-3">
        <Panel title="Automation progress" subtitle={`${initialization.progress}% complete`}>
          <div className="px-3 pt-3">
            <div
              role="progressbar"
              aria-label="Automation progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={initialization.progress}
              className="h-1.5 overflow-hidden rounded-full bg-surface-3"
            >
              <div
                className="h-full bg-accent"
                style={{ width: `${initialization.progress}%` }}
              />
            </div>
          </div>
          <div className="grid gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_16rem]">
            <ol className="space-y-2">
              {initialization.stages.map((stage, index) => (
                <Stage key={stage.label} stage={stage} index={index} />
              ))}
            </ol>
            <div className="space-y-2">
              <Metric label="Current action" value={status?.automation?.last_action ?? initialization.headline} severity={initialization.tone} />
              <Metric label="Market phase" value={status?.market_phase ?? "--"} />
              <Metric label="Trading date" value={status?.trading_date ?? "--"} />
            </div>
          </div>
        </Panel>

        {status === null && (
          <StateMessage title="Backend unreachable" severity="danger" role="alert">
            Check the backend service and NEXT_PUBLIC_BACKEND_URL. This page will retry when it becomes visible.
          </StateMessage>
        )}
        {status && !status.configured && (
          <StateMessage title="Backend configuration required" severity="danger" role="alert">
            Add the required backend environment values before downloader initialization can continue.
          </StateMessage>
        )}
        {status && <AutomationStatus status={status} />}
      </div>
    </PageFrame>
  );
}

function Stage({ stage, index }: { stage: InitializationStage; index: number }) {
  return (
    <li className="grid grid-cols-[2rem_1fr] gap-3 rounded-lg border border-border bg-surface-2 p-3">
      <span className={`grid h-8 w-8 place-items-center rounded-lg border border-border font-mono text-xs status-${STAGE_SEVERITY[stage.state]}`}>
        {stage.state === "complete" ? "OK" : index + 1}
      </span>
      <div>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <p className="text-sm font-semibold text-primary">{stage.label}</p>
          <span className="justify-self-start sm:justify-self-end">
            <StatusIndicator compact label={stage.state} severity={STAGE_SEVERITY[stage.state]} />
          </span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-secondary">{stage.detail}</p>
      </div>
    </li>
  );
}

function AutomationStatus({ status }: { status: AuthStatus }) {
  const hasError = Boolean(status.automation?.last_error);
  return (
    <Panel title="Daily automation" subtitle="server-owned schedule">
      <div className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Kite token" value={status.authenticated ? "validated" : "pending"} severity={status.authenticated ? "success" : "warning"} />
        <Metric label="Token broker" value={status.external_token_source_configured ? "configured" : "missing"} severity={status.external_token_source_configured ? "success" : "danger"} />
        <Metric label="Capture" value={status.capture?.running ? "running" : status.capture_ready ? "ready" : "waiting"} severity={status.capture?.running ? "success" : "neutral"} />
        <Metric label="Risk-free rate" value={status.risk_free_rate == null ? "pending" : `${(status.risk_free_rate * 100).toFixed(4)}%`} />
      </div>
      <div className="border-t border-border p-3">
        <StateMessage title={hasError ? "Automation needs attention" : "Automation status"} severity={hasError ? "warning" : "neutral"}>
          {automationMessage(status.automation, Boolean(status.capture_ready))}
          {status.automation?.last_error ? ` ${status.automation.last_error}` : ""}
        </StateMessage>
      </div>
    </Panel>
  );
}
