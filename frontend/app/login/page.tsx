"use client";

import { useCallback, useEffect, useReducer, useState } from "react";

import {
  ApiError,
  cancelAutomatedLogin,
  completeAutomatedLogin,
  getAuthStatus,
  getLoginUrl,
  postLogin,
  startAutomatedLogin,
  submitLoginTotp,
  type AuthStatus,
  type LoginResult,
} from "@/lib/api";
import {
  initialLoginFlowState,
  isValidTotp,
  loginFlowReducer,
  parseRiskFreeRate,
} from "@/lib/loginFlow";

const LOGIN_ATTEMPT_STORAGE_KEY = "md_login_attempt";

export default function LoginPage() {
  const [status, setStatus] = useState<AuthStatus | null | undefined>(undefined);
  const [flow, dispatch] = useReducer(loginFlowReducer, initialLoginFlowState);
  const [totp, setTotp] = useState("");
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LoginResult | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getAuthStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    let isActive = true;
    getAuthStatus()
      .then((nextStatus) => {
        if (isActive) setStatus(nextStatus);
      })
      .catch(() => {
        if (isActive) setStatus(null);
      });
    getLoginUrl()
      .then((url) => {
        if (isActive) setLoginUrl(url);
      })
      .catch(() => {
        if (isActive) setLoginUrl(null);
      });
    const savedAttempt = readSavedAttempt();
    if (savedAttempt) {
      dispatch({
        type: "started",
        attemptId: savedAttempt.attemptId,
        backendStep: savedAttempt.step,
        method: savedAttempt.method,
      });
    }
    return () => {
      isActive = false;
    };
  }, []);

  const start = async () => {
    dispatch({ type: "start" });
    setBusy(true);
    setResult(null);
    try {
      const response = await startAutomatedLogin();
      if ("authenticated" in response) {
        clearSavedAttempt();
        setResult(response);
        dispatch({ type: "succeeded" });
        await refresh();
        return;
      }
      saveAttempt(response.attempt_id, response.step, response.method, response.expires_at);
      dispatch({
        type: "started",
        attemptId: response.attempt_id,
        backendStep: response.step,
        method: response.method,
      });
    } catch (error) {
      setTotp("");
      clearSavedAttempt();
      dispatch({
        type: "failedAndReset",
        message: `${errorMessage(error)} Start the automated login again.`,
      });
    } finally {
      setBusy(false);
    }
  };

  const submitTotp = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!isValidTotp(totp)) {
      dispatch({ type: "failed", message: "Enter exactly 6 digits from your authenticator." });
      return;
    }
    if (!flow.attemptId) return;
    setBusy(true);
    try {
      const response = await submitLoginTotp(flow.attemptId, totp);
      setTotp("");
      saveAttempt(flow.attemptId, response.step, response.method, response.expires_at);
      dispatch({
        type: "started",
        attemptId: flow.attemptId,
        backendStep: response.step,
        method: response.method,
      });
    } catch (error) {
      if (error instanceof ApiError) {
        setTotp("");
        clearSavedAttempt();
        dispatch({
          type: "failedAndReset",
          message: `${errorMessage(error)} Start the automated login again.`,
        });
      } else {
        dispatch({ type: "failed", message: errorMessage(error) });
      }
    } finally {
      setBusy(false);
    }
  };

  const submitRate = async (event: React.FormEvent) => {
    event.preventDefault();
    const parsedRate = parseRiskFreeRate(rate);
    if (parsedRate === null) {
      dispatch({ type: "failed", message: "Enter a finite non-negative decimal rate." });
      return;
    }
    if (!flow.attemptId) return;
    setBusy(true);
    try {
      const response = await completeAutomatedLogin(flow.attemptId, parsedRate);
      clearSavedAttempt();
      setResult(response);
      dispatch({ type: "succeeded" });
      await refresh();
    } catch (error) {
      if (error instanceof ApiError) {
        clearSavedAttempt();
        dispatch({
          type: "failedAndReset",
          message: `${errorMessage(error)} Start the automated login again.`,
        });
      } else {
        dispatch({ type: "failed", message: errorMessage(error) });
      }
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (flow.attemptId && flow.step !== "success") {
      try {
        await cancelAutomatedLogin(flow.attemptId);
      } catch (error) {
        dispatch({ type: "failed", message: `Could not cancel login: ${errorMessage(error)}` });
        return;
      }
    }
    setTotp("");
    setRate("");
    setResult(null);
    clearSavedAttempt();
    dispatch({ type: "reset" });
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 py-4">
      <div>
        <h1 className="text-xl font-semibold text-zinc-100">Kite Login</h1>
        <p className="mt-1 text-sm text-zinc-400">
          The backend checks the shared VPS session first, then falls back to its local credentials.
          Access tokens and backend credentials never enter this UI.
        </p>
      </div>

      <StatusCard status={status} />
      <LoginSteps currentStep={flow.step} />

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        {(flow.step === "idle" || flow.step === "starting") && (
          <StartStep status={status} busy={busy} onStart={start} />
        )}
        {flow.step === "totp" && (
          <TotpStep value={totp} busy={busy} onChange={setTotp} onSubmit={submitTotp} onCancel={reset} />
        )}
        {flow.step === "rate" && (
          <RateStep
            value={rate}
            busy={busy}
            method={flow.method}
            onChange={setRate}
            onSubmit={submitRate}
            onCancel={reset}
          />
        )}
        {flow.step === "success" && <SuccessStep result={result} onReset={reset} />}
        {flow.error && <p className="mt-4 text-sm text-red-400" role="alert">{flow.error}</p>}
      </section>

      <TerminalLoginHelp />
      {flow.step === "idle" && <BrowserFallback loginUrl={loginUrl} onSuccess={refresh} />}
    </div>
  );
}

function StartStep({
  status,
  busy,
  onStart,
}: {
  status: AuthStatus | null | undefined;
  busy: boolean;
  onStart: () => Promise<void>;
}) {
  const isReady = Boolean(
    status?.configured &&
      (status.credentials_present || status.external_token_source_configured),
  );
  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-300">
        Start by checking the shared VPS session. If it has no active token, the backend
        continues with KITE_USER_ID and KITE_PASSWORD and asks for your TOTP.
      </p>
      <button
        type="button"
        disabled={!isReady || busy}
        onClick={() => void onStart()}
        className="rounded-md bg-sky-500 px-4 py-2 font-semibold text-sky-950 disabled:opacity-50"
      >
        {busy ? "Checking shared session…" : "Start login"}
      </button>
      {!isReady && status && (
        <p className="text-xs text-amber-400">
          Configure the VPS token source or backend login credentials before starting.
        </p>
      )}
    </div>
  );
}

function TotpStep({ value, busy, onChange, onSubmit, onCancel }: StepFormProps) {
  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <p className="text-sm text-green-400">Environment credentials accepted. Enter your TOTP.</p>
      <Field label="Authenticator TOTP" hint="Required: exactly 6 digits.">
        <input
          autoFocus
          required
          value={value}
          onChange={(event) => onChange(event.target.value.replace(/[^0-9]/g, ""))}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          placeholder="123456"
          className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
        />
      </Field>
      <SubmitButton busy={busy} busyLabel="Verifying TOTP…" label="Verify TOTP" />
      <CancelButton busy={busy} onCancel={onCancel} />
    </form>
  );
}

function RateStep({
  value,
  busy,
  method,
  onChange,
  onSubmit,
  onCancel,
}: StepFormProps & { method: "shared_session" | "local_credentials" | null }) {
  const confirmation = method === "shared_session"
    ? "Shared VPS session verified. Confirm today's risk-free rate to activate it here."
    : "TOTP verified and the access token was issued. Confirm today's risk-free rate.";
  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <p className="text-sm text-green-400">{confirmation}</p>
      <Field label="10-year bond yield" hint="Enter the decimal value, for example 0.0691.">
        <input
          autoFocus
          required
          value={value}
          onChange={(event) => onChange(event.target.value)}
          inputMode="decimal"
          placeholder="0.0691"
          className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
        />
      </Field>
      <SubmitButton busy={busy} busyLabel="Completing login…" label="Complete login" />
      <CancelButton busy={busy} onCancel={onCancel} />
    </form>
  );
}

function SuccessStep({ result, onReset }: { result: LoginResult | null; onReset: () => Promise<void> }) {
  return (
    <div className="space-y-3" role="status">
      <p className="font-semibold text-green-400">Login cycle completed successfully.</p>
      <p className="text-sm text-zinc-300">
        Session {result?.trading_date ?? "today"} · rate {result?.risk_free_rate ?? "confirmed"}
      </p>
      <button type="button" onClick={() => void onReset()} className="text-sm text-sky-400 underline">
        Start another login
      </button>
    </div>
  );
}

function LoginSteps({ currentStep }: { currentStep: string }) {
  const activeIndex = currentStep === "totp" ? 1 : currentStep === "rate" ? 2 : currentStep === "success" ? 3 : 0;
  return (
    <ol className="grid grid-cols-3 gap-2" aria-label="Login progress">
      {["Authenticate", "Risk-free rate", "Success"].map((label, index) => (
        <li
          key={label}
          aria-current={index + 1 === activeIndex ? "step" : undefined}
          className={`rounded-md border px-3 py-2 text-xs ${index + 1 <= activeIndex ? "border-green-700 bg-green-950/30 text-green-300" : "border-zinc-800 text-zinc-500"}`}
        >
          {index + 1}. {label}
        </li>
      ))}
    </ol>
  );
}

function TerminalLoginHelp() {
  return (
    <details className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-zinc-400">
      <summary className="cursor-pointer text-zinc-300">Use the terminal instead</summary>
      <p className="mt-3">
        Run <code className="text-zinc-200">md-login</code> in the backend terminal. It
        checks the shared session first, then prompts for the required next step.
      </p>
    </details>
  );
}

function BrowserFallback({ loginUrl, onSuccess }: { loginUrl: string | null; onSuccess: () => Promise<void> }) {
  const [requestToken, setRequestToken] = useState("");
  const [rate, setRate] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const parsedRate = parseRiskFreeRate(rate);
    if (!requestToken.trim() || parsedRate === null) return setMessage("Token and rate are required.");
    setBusy(true);
    try {
      await postLogin({ request_token: requestToken.trim(), risk_free_rate: parsedRate });
      setMessage("Browser fallback login completed.");
      await onSuccess();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-zinc-400">
      <summary className="cursor-pointer text-zinc-300">Browser OAuth fallback</summary>
      <form onSubmit={submit} className="mt-3 space-y-3">
        {loginUrl && <a href={loginUrl} target="_blank" rel="noreferrer" className="text-sky-400 underline">Open Zerodha login →</a>}
        <Field label="Request token"><input required value={requestToken} onChange={(event) => setRequestToken(event.target.value)} placeholder="request_token" className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2" /></Field>
        <Field label="Risk-free rate"><input required value={rate} onChange={(event) => setRate(event.target.value)} placeholder="0.0691" className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2" /></Field>
        <button disabled={busy} className="rounded-md border border-zinc-700 px-3 py-2 text-zinc-200 disabled:opacity-50">{busy ? "Completing…" : "Complete fallback login"}</button>
        {message && <p role="status">{message}</p>}
      </form>
    </details>
  );
}

interface StepFormProps {
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onSubmit: (event: React.FormEvent) => Promise<void>;
  onCancel: () => Promise<void>;
}

function SubmitButton({ busy, busyLabel, label }: { busy: boolean; busyLabel: string; label: string }) {
  return <button disabled={busy} className="rounded-md bg-sky-500 px-4 py-2 font-semibold text-sky-950 disabled:opacity-50">{busy ? busyLabel : label}</button>;
}

function CancelButton({ busy, onCancel }: { busy: boolean; onCancel: () => Promise<void> }) {
  return <button type="button" disabled={busy} onClick={() => void onCancel()} className="ml-3 text-sm text-zinc-400 underline disabled:opacity-50">Cancel and start over</button>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">{label}</span>{children}{hint && <span className="mt-1 block text-xs text-zinc-600">{hint}</span>}</label>;
}

function StatusCard({ status }: { status: AuthStatus | null | undefined }) {
  if (status === undefined) return <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm text-zinc-300" role="status">Checking backend connection…</div>;
  if (status === null) return <div className="rounded-xl border border-red-900/50 bg-red-950/20 p-4 text-sm text-red-300" role="alert">Backend unreachable. Check the backend service and NEXT_PUBLIC_BACKEND_URL.</div>;
  if (!status.configured) return <div className="rounded-xl border border-amber-900/50 bg-amber-950/20 p-4 text-sm text-amber-300" role="alert">Backend is online but not configured. Check its required environment variables.</div>;
  return <div className="grid grid-cols-2 gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm sm:grid-cols-3"><Stat label="Trading date" value={status.trading_date ?? "–"} /><Stat label="Market phase" value={status.market_phase ?? "–"} /><Stat label="Session" value={status.authenticated ? "active" : "not logged in"} tone={status.authenticated ? "green" : "amber"} /><Stat label="Shared token source" value={status.external_token_source_configured ? "configured" : "not set"} /><Stat label="Credentials" value={status.credentials_present ? "ready" : "missing"} /><Stat label="Static IP" value={status.static_ip_configured ? "ready" : "not set"} /></div>;
}

function Stat({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "green" | "amber" }) {
  const color = tone === "green" ? "text-green-400" : tone === "amber" ? "text-amber-400" : "text-zinc-200";
  return <div className="flex flex-col"><span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span><span className={`font-semibold ${color}`}>{value}</span></div>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function saveAttempt(
  attemptId: string,
  step: "awaiting_totp" | "awaiting_risk_free_rate",
  method: "shared_session" | "local_credentials",
  expiresAt: number,
) {
  window.sessionStorage.setItem(
    LOGIN_ATTEMPT_STORAGE_KEY,
    JSON.stringify({ attemptId, step, method, expiresAt }),
  );
}

function clearSavedAttempt() {
  window.sessionStorage.removeItem(LOGIN_ATTEMPT_STORAGE_KEY);
}

function readSavedAttempt(): {
  attemptId: string;
  step: "awaiting_totp" | "awaiting_risk_free_rate";
  method: "shared_session" | "local_credentials";
} | null {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(LOGIN_ATTEMPT_STORAGE_KEY) ?? "null");
    const isValidStep = ["awaiting_totp", "awaiting_risk_free_rate"].includes(parsed?.step);
    const isValidMethod = ["shared_session", "local_credentials"].includes(parsed?.method);
    if (!parsed?.attemptId || !isValidStep || !isValidMethod || !Number.isFinite(parsed.expiresAt)) {
      clearSavedAttempt();
      return null;
    }
    if (parsed.expiresAt <= Date.now()) {
      clearSavedAttempt();
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}
