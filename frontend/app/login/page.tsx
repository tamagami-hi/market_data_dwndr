"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { getAuthStatus, updateRiskFreeRate, type AuthStatus } from "@/lib/api";
import { automationMessage } from "@/lib/automationStatus";
import { parseRiskFreeRate } from "@/lib/loginFlow";

const STATUS_REFRESH_MS = 5_000;

type StageState = "pending" | "active" | "complete" | "error";

interface InitializationStage {
  label: string;
  detail: string;
  state: StageState;
}

export default function LoginPage() {
  const [status, setStatus] = useState<AuthStatus | null | undefined>(undefined);
  const [updatedRate, setUpdatedRate] = useState("");
  const [rateBusy, setRateBusy] = useState(false);
  const [rateUpdateMessage, setRateUpdateMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getAuthStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    let isActive = true;
    const poll = async () => {
      try {
        const nextStatus = await getAuthStatus();
        if (isActive) setStatus(nextStatus);
      } catch {
        if (isActive) setStatus(null);
      }
    };
    void poll();
    const refreshTimer = window.setInterval(() => void poll(), STATUS_REFRESH_MS);
    return () => {
      isActive = false;
      window.clearInterval(refreshTimer);
    };
  }, []);

  const updateRate = async (event: FormEvent) => {
    event.preventDefault();
    const parsedRate = parseRiskFreeRate(updatedRate);
    if (parsedRate === null) {
      setRateUpdateMessage("Enter the decimal yield between 0 and 1.");
      return;
    }
    setRateBusy(true);
    setRateUpdateMessage(null);
    try {
      await updateRiskFreeRate(parsedRate);
      setUpdatedRate("");
      setRateUpdateMessage("Yield updated. Automatic capture is now ready.");
      await refresh();
    } catch (error) {
      setRateUpdateMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setRateBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 py-4">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-400">
          Unattended VPS service
        </p>
        <h1 className="mt-2 text-xl font-semibold text-zinc-100">Initializing downloader</h1>
        <p className="mt-1 text-sm text-zinc-400">
          The backend fetches the daily Kite token from the configured secure token broker,
          validates it, and starts capture automatically inside the market window.
        </p>
      </header>

      <InitializationProgress status={status} />
      <StatusCard status={status} />

      {status && <AutomationCard status={status} />}
      {rateUpdateMessage && !status?.rate_update_required && (
        <p
          className="rounded-xl border border-green-800/60 bg-green-950/20 p-4 text-sm text-green-300"
          role="status"
        >
          {rateUpdateMessage}
        </p>
      )}
      {status?.authenticated && status.rate_update_required && (
        <YieldUpdateForm
          value={updatedRate}
          busy={rateBusy}
          message={rateUpdateMessage}
          hasPreviousYield={Boolean(status.risk_free_rate_as_of)}
          onChange={setUpdatedRate}
          onSubmit={updateRate}
        />
      )}
    </div>
  );
}

function InitializationProgress({ status }: { status: AuthStatus | null | undefined }) {
  const { progress, headline, stages } = initializationState(status);
  const hasError = stages.some((stage) => stage.state === "error");

  return (
    <section
      className={`rounded-xl border p-5 ${
        hasError
          ? "border-red-900/60 bg-red-950/20"
          : "border-sky-900/60 bg-sky-950/20"
      }`}
      aria-live="polite"
    >
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm font-medium text-zinc-100">{headline}</p>
        <span className="font-mono text-sm text-sky-300">{progress}%</span>
      </div>
      <div
        className="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800"
        role="progressbar"
        aria-label="Downloader initialization"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            hasError ? "bg-red-500" : "bg-sky-400"
          }`}
          style={{ width: `${progress}%` }}
        />
      </div>
      <ol className="mt-5 grid gap-3 sm:grid-cols-2">
        {stages.map((stage) => (
          <li key={stage.label} className="flex gap-3 rounded-lg border border-zinc-800/80 bg-zinc-950/40 p-3">
            <StageDot state={stage.state} />
            <div>
              <p className="text-sm font-medium text-zinc-200">{stage.label}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">{stage.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function initializationState(status: AuthStatus | null | undefined): {
  progress: number;
  headline: string;
  stages: InitializationStage[];
} {
  if (status === undefined) {
    return {
      progress: 8,
      headline: "Connecting to the downloader service…",
      stages: baseStages("active", "pending", "pending", "pending"),
    };
  }
  if (status === null) {
    return {
      progress: 0,
      headline: "Backend is unreachable",
      stages: baseStages("error", "pending", "pending", "pending"),
    };
  }
  if (!status.configured) {
    return {
      progress: 15,
      headline: "Backend environment is incomplete",
      stages: baseStages("error", "pending", "pending", "pending"),
    };
  }
  if (!status.external_token_source_configured && !status.authenticated) {
    const stages = baseStages("complete", "error", "pending", "pending");
    stages[1].detail = "Configure KITE_TOKEN_BROKER_URL and its passcode in backend/.env.";
    return { progress: 25, headline: "Secure token broker is not configured", stages };
  }
  if (!status.authenticated) {
    const attempted = Boolean(status.automation?.last_broker_poll_at);
    const stages = baseStages("complete", "complete", attempted ? "active" : "pending", "pending");
    stages[2].detail = status.automation?.last_error
      ? status.automation.last_error
      : attempted
        ? "Token received or pending; the backend validates it directly with Kite before saving it."
        : "Waiting for the configured trading-day token polling window.";
    return {
      progress: attempted ? 55 : 40,
      headline: attempted ? "Fetching and validating the daily token…" : "Waiting to fetch the daily token",
      stages,
    };
  }
  if (status.rate_update_required || !status.capture_ready) {
    const brokerConfigured = Boolean(status.external_token_source_configured);
    const stages = baseStages("complete", brokerConfigured ? "complete" : "pending", "complete", "active");
    if (!brokerConfigured) {
      stages[1].detail = "Not configured; this validated session came from the retained fallback flow.";
    }
    stages[2].detail = "Kite accepted the token and the daily session is saved.";
    stages[3].detail = "A current 10-year yield is required before capture can initialize.";
    return { progress: 75, headline: "Token validated; capture prerequisites pending", stages };
  }

  const running = Boolean(status.capture?.running);
  const brokerConfigured = Boolean(status.external_token_source_configured);
  const stages = baseStages("complete", brokerConfigured ? "complete" : "pending", "complete", "complete");
  if (!brokerConfigured) {
    stages[1].detail = "Not configured; this validated session came from the retained fallback flow.";
  }
  stages[2].detail = "Kite accepted the token and the daily session is saved.";
  stages[3].detail = running
    ? `Capturing ${status.capture?.tokens ?? 0} subscribed instruments for ${status.capture?.trading_date ?? "today"}.`
    : "Downloader is ready and the scheduler is waiting for the configured market window.";
  return {
    progress: 100,
    headline: running ? "Downloader is running" : "Downloader initialized",
    stages,
  };
}

function baseStages(
  backend: StageState,
  broker: StageState,
  token: StageState,
  downloader: StageState,
): InitializationStage[] {
  return [
    { label: "Backend configuration", detail: "Environment and storage paths loaded.", state: backend },
    { label: "Secure token broker", detail: "HTTPS token source and passcode configured.", state: broker },
    { label: "Token fetch and validation", detail: "Waiting for a validated daily Kite session.", state: token },
    { label: "Downloader", detail: "Waiting for capture prerequisites and market hours.", state: downloader },
  ];
}

function StageDot({ state }: { state: StageState }) {
  const color = {
    pending: "bg-zinc-600",
    active: "bg-sky-400 shadow-[0_0_8px] shadow-sky-400",
    complete: "bg-green-500",
    error: "bg-red-500",
  }[state];
  return <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${color}`} aria-hidden="true" />;
}

function AutomationCard({ status }: { status: AuthStatus }) {
  const message = automationMessage(
    status.automation,
    Boolean(status.capture_ready),
    Boolean(status.rate_update_required),
  );
  const tone = status.rate_update_required || status.automation?.last_error
    ? "border-amber-900/60 bg-amber-950/20 text-amber-300"
    : "border-cyan-900/60 bg-cyan-950/20 text-cyan-200";
  return (
    <section className={`rounded-xl border p-4 text-sm ${tone}`} aria-live="polite">
      <p className="font-medium">Daily downloader automation</p>
      <p className="mt-1">{message}</p>
      {status.risk_free_rate != null && (
        <p className="mt-2 text-xs opacity-80">
          10-year yield {status.risk_free_rate} · valid from {status.risk_free_rate_as_of ?? "today"}
        </p>
      )}
    </section>
  );
}

function YieldUpdateForm({
  value,
  busy,
  message,
  hasPreviousYield,
  onChange,
  onSubmit,
}: {
  value: string;
  busy: boolean;
  message: string | null;
  hasPreviousYield: boolean;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent) => Promise<void>;
}) {
  return (
    <form onSubmit={onSubmit} className="space-y-3 rounded-xl border border-amber-800/60 bg-amber-950/20 p-5">
      <div>
        <h2 className="font-semibold text-amber-200">10-year bond yield update required</h2>
        <p className="mt-1 text-sm text-amber-300/80">
          {hasPreviousYield
            ? "This is the third Monday–Friday market day for the stored yield."
            : "No 10-year yield is stored yet."}
          {" "}Update it to allow automatic capture; the validated broker token remains active.
        </p>
      </div>
      <Field label="Today&apos;s 10-year bond yield" hint="Decimal form, for example 0.0691.">
        <input
          required
          value={value}
          onChange={(event) => onChange(event.target.value)}
          inputMode="decimal"
          placeholder="0.0691"
          className="w-full rounded-md border border-amber-800 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
        />
      </Field>
      <button disabled={busy} className="rounded-md bg-amber-400 px-4 py-2 font-semibold text-amber-950 disabled:opacity-50">
        {busy ? "Updating yield…" : "Update yield and enable capture"}
      </button>
      {message && <p role="status" className="text-sm text-amber-200">{message}</p>}
    </form>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-zinc-600">{hint}</span>}
    </label>
  );
}

function StatusCard({ status }: { status: AuthStatus | null | undefined }) {
  if (status === undefined) {
    return <Notice tone="neutral">Checking backend connection…</Notice>;
  }
  if (status === null) {
    return <Notice tone="error">Backend unreachable. Check the backend service and NEXT_PUBLIC_BACKEND_URL.</Notice>;
  }
  if (!status.configured) {
    return <Notice tone="warning">Backend is online but not configured. Check its required environment variables.</Notice>;
  }
  return (
    <div className="grid grid-cols-2 gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm sm:grid-cols-3">
      <Stat label="Trading date" value={status.trading_date ?? "–"} />
      <Stat label="Market phase" value={status.market_phase ?? "–"} />
      <Stat label="Kite token" value={status.authenticated ? "validated" : "pending"} tone={status.authenticated ? "green" : "amber"} />
      <Stat label="Token broker" value={status.external_token_source_configured ? "configured" : "missing"} />
      <Stat label="Capture" value={status.capture?.running ? "running" : status.capture_ready ? "ready" : "waiting"} />
      <Stat label="Static egress" value={status.static_ip_configured ? "ready" : "not set"} />
    </div>
  );
}

function Notice({ tone, children }: { tone: "neutral" | "warning" | "error"; children: ReactNode }) {
  const style = {
    neutral: "border-zinc-800 bg-zinc-900/60 text-zinc-300",
    warning: "border-amber-900/50 bg-amber-950/20 text-amber-300",
    error: "border-red-900/50 bg-red-950/20 text-red-300",
  }[tone];
  return <div className={`rounded-xl border p-4 text-sm ${style}`} role="status">{children}</div>;
}

function Stat({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "green" | "amber" }) {
  const color = tone === "green" ? "text-green-400" : tone === "amber" ? "text-amber-400" : "text-zinc-200";
  return (
    <div className="flex flex-col">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className={`font-semibold ${color}`}>{value}</span>
    </div>
  );
}
