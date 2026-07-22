"use client";

import { useCallback, useEffect, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import { getCaptureHistory, type CaptureHistory } from "@/lib/api";
import { formatBytes, formatClockTime, formatIndianNumber } from "@/lib/numberFormat";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { captureStatusConnection, sessionConnection } from "@/lib/wsTopicConnection";
import {
  MSG,
  type CaptureStatusPayload,
  type GlobalStatus,
  type PerUnderlyingStatus,
  type WsEnvelope,
} from "@/lib/wsTypes";

interface LogLine {
  ts: number;
  text: string;
}

export default function MonitorPage() {
  const [rows, setRows] = useState<PerUnderlyingStatus[]>([]);
  const [globals, setGlobals] = useState<GlobalStatus | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [history, setHistory] = useState<CaptureHistory | null | undefined>(undefined);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const onCaptureStatus = useCallback((env: WsEnvelope) => {
    if (env.type !== MSG.CAPTURE_STATUS) return;
    const payload = env.payload as CaptureStatusPayload;
    setRows(payload.per_underlying ?? []);
    setGlobals(payload.global ?? null);
  }, []);

  const onSession = useCallback((env: WsEnvelope) => {
    if (env.type === MSG.LOG) {
      const msg = (env.payload as { message?: string })?.message ?? "";
      setLogs((prev) => [{ ts: Date.now(), text: msg }, ...prev].slice(0, 200));
    } else if (env.type === MSG.SESSION_STATUS) {
      const phase = (env.payload as { phase?: string })?.phase ?? "";
      setLogs((prev) => [{ ts: Date.now(), text: `session: ${phase}` }, ...prev].slice(0, 200));
    }
  }, []);

  useTopicEnvelopes(captureStatusConnection, onCaptureStatus);
  useTopicEnvelopes(sessionConnection, onSession);

  useEffect(() => {
    let isActive = true;
    const poll = async () => {
      try {
        const nextHistory = await getCaptureHistory();
        if (isActive) {
          setHistory(nextHistory.available ? nextHistory : null);
          setHistoryError(nextHistory.available ? null : "Capture history is unavailable until the backend is configured.");
        }
      } catch (error) {
        if (isActive) {
          setHistoryError(error instanceof Error ? error.message : "Capture history refresh failed.");
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 15_000);
    return () => {
      isActive = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="space-y-6">
      <header className="flex items-center gap-4">
        <h1 className="text-xl font-semibold text-zinc-100">Capture Monitor</h1>
        <div className="ml-auto flex items-center gap-4">
          <ConnectionDot connection={captureStatusConnection} label="capture-status" />
          <ConnectionDot connection={sessionConnection} label="session" />
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Tokens" value={globals ? formatIndianNumber(globals.tokens, 0) : "–"} />
        <Metric label="Frames / sec" value={globals ? globals.fps.toFixed(2) : "–"} />
        <Metric label="Captures" value={globals ? formatIndianNumber(globals.captures, 0) : "–"} />
        <Metric
          label="Disk (MARKET_DATA)"
          value={globals ? formatBytes(globals.disk_bytes) : "–"}
        />
      </section>

      <DownloadHistory history={history} error={historyError} />

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Per underlying
        </h2>
        {rows.length === 0 ? (
          <EmptyState message="Waiting for automatic capture telemetry from the market-hours scheduler…" />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {rows.map((row) => (
              <UnderlyingCard key={row.underlying} row={row} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Session / log
        </h2>
        <div className="h-48 overflow-auto rounded-lg border border-zinc-800 bg-black/40 p-3 font-mono text-xs">
          {logs.length === 0 ? (
            <span className="text-zinc-600">No session messages yet.</span>
          ) : (
            logs.map((line, i) => (
              <div key={`${line.ts}-${i}`} className="text-zinc-400">
                <span className="text-sky-400">{new Date(line.ts).toLocaleTimeString()}</span>{" "}
                {line.text}
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function DownloadHistory({
  history,
  error,
}: {
  history: CaptureHistory | null | undefined;
  error: string | null;
}) {
  if (history === undefined || history === null) {
    const message = error ?? "Loading capture history from live and archive storage…";
    return (
      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Download history
        </h2>
        <EmptyState message={message} />
      </section>
    );
  }

  const archiveShare = history.totals.total_bytes > 0
    ? (history.totals.archived_bytes / history.totals.total_bytes) * 100
    : 0;

  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Download history
          </h2>
          <p className="mt-1 text-sm text-zinc-400">
            Live raw captures and verified archives, refreshed while the service runs.
          </p>
        </div>
        {error && history && (
          <span className="text-xs text-amber-500">Refresh delayed; showing the last successful snapshot.</span>
        )}
        {history.generated_at && (
          <span className="text-xs text-zinc-600">
            Updated {formatClockTime(history.generated_at)}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Sessions" value={formatIndianNumber(history.totals.sessions, 0)} />
        <Metric label="Data files" value={formatIndianNumber(history.totals.data_files, 0)} />
        <Metric label="Stored data" value={formatBytes(history.totals.total_bytes)} />
        <Metric label="Archived share" value={`${archiveShare.toFixed(1)}%`} />
      </div>

      {history.sessions.length === 0 ? (
        <EmptyState message="No completed or active capture sessions are stored yet." />
      ) : (
        <div className="max-h-96 overflow-auto rounded-xl border border-zinc-800 bg-zinc-900/40">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="sticky top-0 bg-zinc-950 text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-4 py-3">Session</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3">Stored</th>
                <th className="px-4 py-3">Raw / archive</th>
                <th className="px-4 py-3">Files</th>
                <th className="px-4 py-3">Captured sets</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {history.sessions.map((session) => {
                const state = session.raw_files > 0 && session.archived_files > 0
                  ? "Archiving"
                  : session.raw_files > 0
                    ? session.is_current ? "Recording" : "Raw"
                    : "Archived";
                return (
                  <tr key={session.trading_date} className="text-zinc-300">
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-zinc-100">
                      {session.trading_date}
                      {session.is_current && (
                        <span className="ml-2 rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] text-sky-300">
                          current
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">{state}</td>
                    <td className="whitespace-nowrap px-4 py-3">{formatBytes(session.total_bytes)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-zinc-500">
                      {formatBytes(session.raw_bytes)} / {formatBytes(session.archived_bytes)}
                    </td>
                    <td className="px-4 py-3">{formatIndianNumber(session.data_files, 0)}</td>
                    <td className="px-4 py-3 text-xs text-zinc-400">
                      {session.indices.length > 0 ? session.indices.join(", ") : "No indices"}
                      {session.stock_files > 0 ? ` · stocks (${session.stock_files})` : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1.5 text-2xl font-bold text-zinc-100">{value}</div>
    </div>
  );
}

function UnderlyingCard({ row }: { row: PerUnderlyingStatus }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${
            row.connected ? "bg-green-500 shadow-[0_0_6px] shadow-green-500" : "bg-red-500"
          }`}
        />
        <span className="font-semibold text-zinc-100">{row.underlying}</span>
        <span
          className={`ml-auto rounded-full px-2 py-0.5 text-[11px] ${
            row.heartbeat_ok
              ? "bg-green-500/15 text-green-400"
              : "bg-red-500/15 text-red-400"
          }`}
        >
          {row.heartbeat_ok ? "1 Hz" : "stale"}
        </span>
      </div>
      <Row label="Frames today" value={formatIndianNumber(row.frames_written, 0)} />
      <Row label="File size" value={formatBytes(row.file_bytes)} />
      <Row label="Last tick" value={formatClockTime(row.last_tick_ms)} />
      <Row label="Unmatched" value={formatIndianNumber(row.unmatched, 0)} />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-0.5 text-sm">
      <span className="text-zinc-500">{label}</span>
      <span className="font-medium text-zinc-200">{value}</span>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-900/30 p-8 text-center text-sm text-zinc-500">
      {message}
    </div>
  );
}
