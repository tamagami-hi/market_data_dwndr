"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getAuthStatus,
  getLoginUrl,
  postLogin,
  type AuthStatus,
  type LoginResult,
} from "@/lib/api";

export default function LoginPage() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [totp, setTotp] = useState("");
  const [rate, setRate] = useState("");
  const [requestToken, setRequestToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    let alive = true;
    (async () => {
      try {
        const s = await getAuthStatus();
        if (alive) setStatus(s);
      } catch {
        if (alive) setStatus(null);
      }
    })();
    getLoginUrl()
      .then((u) => {
        if (alive) setLoginUrl(u);
      })
      .catch(() => {
        if (alive) setLoginUrl(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await postLogin({
        totp: totp.trim() || undefined,
        request_token: requestToken.trim() || undefined,
        risk_free_rate: rate.trim() ? Number(rate) : undefined,
      });
      setResult(res);
      setTotp("");
      setRequestToken("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-xl space-y-6 py-4">
      <h1 className="text-xl font-semibold text-zinc-100">Kite Login</h1>

      <StatusCard status={status} />

      <form onSubmit={submit} className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <p className="text-sm text-zinc-400">
          Credentials are read from the backend&apos;s environment. Enter the current 6-digit
          TOTP from your authenticator to complete the automated login.
        </p>

        <Field label="TOTP (6-digit)">
          <input
            value={totp}
            onChange={(e) => setTotp(e.target.value)}
            inputMode="numeric"
            maxLength={6}
            placeholder="123456"
            className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
          />
        </Field>

        <Field label="10-yr bond yield (decimal)" hint="Optional if RISK_FREE_RATE is set in env.">
          <input
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            placeholder="0.0691"
            className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
          />
        </Field>

        <details className="text-sm text-zinc-400">
          <summary className="cursor-pointer select-none">Browser OAuth fallback</summary>
          <div className="mt-3 space-y-3">
            {loginUrl && (
              <a
                href={loginUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-block text-sky-400 underline"
              >
                Open Zerodha login →
              </a>
            )}
            <Field label="request_token (from redirect URL)">
              <input
                value={requestToken}
                onChange={(e) => setRequestToken(e.target.value)}
                placeholder="paste request_token"
                className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-zinc-100"
              />
            </Field>
          </div>
        </details>

        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-sky-500 px-4 py-2 font-semibold text-sky-950 disabled:opacity-50"
        >
          {busy ? "Logging in…" : "Log in"}
        </button>

        {error && <p className="text-sm text-red-400">{error}</p>}
        {result?.authenticated && (
          <p className="text-sm text-green-400">
            Logged in for {result.trading_date} (token {result.access_token}).
          </p>
        )}
      </form>
    </div>
  );
}

function StatusCard({ status }: { status: AuthStatus | null }) {
  if (!status) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm text-zinc-400">
        Backend unreachable.
      </div>
    );
  }
  if (!status.configured) {
    return (
      <div className="rounded-xl border border-amber-800/50 bg-amber-950/30 p-4 text-sm text-amber-300">
        Backend is not configured — set the required env vars (KITE_API_KEY / SECRET /
        MARKET_DATA_PATH) and restart.
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm sm:grid-cols-3">
      <Stat label="Trading date" value={status.trading_date ?? "–"} />
      <Stat label="Market phase" value={status.market_phase ?? "–"} />
      <Stat
        label="Session"
        value={status.authenticated ? "active" : "not logged in"}
        tone={status.authenticated ? "green" : "amber"}
      />
      <Stat label="Creds in env" value={status.credentials_present ? "yes" : "no"} />
      <Stat label="TOTP secret" value={status.has_totp_secret ? "yes" : "prompt"} />
      <Stat label="Static IP" value={status.static_ip_configured ? "yes" : "no"} />
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-zinc-600">{hint}</span>}
    </label>
  );
}

function Stat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "green" | "amber";
}) {
  const color =
    tone === "green" ? "text-green-400" : tone === "amber" ? "text-amber-400" : "text-zinc-200";
  return (
    <div className="flex flex-col">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className={`font-semibold ${color}`}>{value}</span>
    </div>
  );
}
