"use client";

import { useCallback, useEffect, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import {
  getCaptureHistory,
  getStats,
  type CaptureHistory,
  type CompressionHistory,
  type DashboardStats,
} from "@/lib/api";
import {
  formatBytes,
  formatClockTime,
  formatDuration,
  formatIndianNumber,
  formatPercent,
  formatThroughput,
  formatUptime,
} from "@/lib/numberFormat";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { captureStatusConnection, sessionConnection } from "@/lib/wsTopicConnection";
import {
  MSG,
  type CaptureStatusPayload,
  type CompressionProgressPayload,
  type GlobalStatus,
  type PerUnderlyingStatus,
  type WsEnvelope,
} from "@/lib/wsTypes";

interface LogLine {
  ts: number;
  text: string;
  kind: "log" | "session";
}

const MAX_LOGS = 300;
const SPARK_SAMPLES = 60;

export default function MonitorPage() {
  const [rows, setRows] = useState<PerUnderlyingStatus[]>([]);
  const [globals, setGlobals] = useState<GlobalStatus | null>(null);
  const [compression, setCompression] = useState<CompressionProgressPayload | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [overlay, setOverlay] = useState<null | "logs">(null);
  const [fpsHistory, setFpsHistory] = useState<number[]>([]);

  const onCaptureStatus = useCallback((env: WsEnvelope) => {
    if (env.type === MSG.CAPTURE_STATUS) {
      const payload = env.payload as CaptureStatusPayload;
      setRows(payload.per_underlying ?? []);
      setGlobals(payload.global ?? null);
      if (payload.global) {
        setFpsHistory((prev) => [...prev, payload.global.fps].slice(-SPARK_SAMPLES));
      }
    } else if (env.type === MSG.COMPRESSION_PROGRESS) {
      setCompression(env.payload as CompressionProgressPayload);
    }
  }, []);

  const onSession = useCallback((env: WsEnvelope) => {
    if (env.type === MSG.LOG) {
      const msg = (env.payload as { message?: string })?.message ?? "";
      setLogs((prev) => [{ ts: Date.now(), text: msg, kind: "log" as const }, ...prev].slice(0, MAX_LOGS));
    } else if (env.type === MSG.SESSION_STATUS) {
      const phase = (env.payload as { phase?: string })?.phase ?? "";
      setLogs((prev) =>
        [{ ts: Date.now(), text: `session: ${phase}`, kind: "session" as const }, ...prev].slice(0, MAX_LOGS),
      );
    }
  }, []);

  useTopicEnvelopes(captureStatusConnection, onCaptureStatus);
  useTopicEnvelopes(sessionConnection, onSession);

  // Poll /api/stats for compression history averages, trading date, and a
  // persisted fallback snapshot when live telemetry is not flowing.
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const next = await getStats();
        if (!active) return;
        setStats(next);
        // Fall back to persisted / current compression when no live WS update yet.
        setCompression((cur) => cur ?? next.compression ?? null);
        if (next.monitor && next.monitor_persisted) {
          setRows((cur) => (cur.length ? cur : next.monitor!.per_underlying ?? []));
          setGlobals((cur) => cur ?? next.monitor!.global ?? null);
        }
      } catch {
        /* transient; keep last good */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 10_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const expectedFrames = stats?.expected_frames_per_session ?? 23_400;
  const history = stats?.compression_history ?? null;
  const tradingDate = stats?.trading_date ?? null;
  const persisted = Boolean(stats && !stats.capture_running && stats.monitor_persisted);

  return (
    <div className="flex h-[calc(100dvh-5.25rem)] flex-col gap-2 overflow-hidden text-zinc-200">
      <TopBar
        globals={globals}
        tradingDate={tradingDate}
        persisted={persisted}
      />

      <KpiStrip globals={globals} fpsHistory={fpsHistory} />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[1fr_1.25fr]">
        <div className="flex min-h-0 flex-col gap-2">
          <FrameIntegrityPanel rows={rows} globals={globals} expectedFrames={expectedFrames} />
          <HistoryPanel />
        </div>
        <div className="flex min-h-0 flex-col gap-2">
          <PerUnderlyingPanel rows={rows} />
          <CompressionPanel current={compression} history={history} />
        </div>
      </div>

      <LogStrip logs={logs} onExpand={() => setOverlay("logs")} />

      {overlay === "logs" && (
        <Overlay title="Session / logs" onClose={() => setOverlay(null)}>
          <FullLogs logs={logs} />
        </Overlay>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Top bar                                                                    */
/* -------------------------------------------------------------------------- */

function TopBar({
  globals,
  tradingDate,
  persisted,
}: {
  globals: GlobalStatus | null;
  tradingDate: string | null;
  persisted: boolean;
}) {
  return (
    <header className="flex flex-shrink-0 items-center gap-4 rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-2">
      <h1 className="text-base font-semibold text-zinc-100">Capture Monitor</h1>
      {tradingDate && (
        <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">{tradingDate}</span>
      )}
      <span className="text-xs text-zinc-500">
        uptime <span className="font-mono text-zinc-300">{formatUptime(globals?.uptime_ms)}</span>
      </span>
      {globals?.ingestion_degraded && (
        <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-medium text-red-400">
          ingestion degraded
        </span>
      )}
      {persisted && (
        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-400">
          last saved snapshot (capture idle)
        </span>
      )}
      <div className="ml-auto flex items-center gap-4">
        <ConnectionDot connection={captureStatusConnection} label="capture" />
        <ConnectionDot connection={sessionConnection} label="session" />
      </div>
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* KPI strip                                                                  */
/* -------------------------------------------------------------------------- */

function KpiStrip({ globals, fpsHistory }: { globals: GlobalStatus | null; fpsHistory: number[] }) {
  const diskPct =
    globals && globals.disk_total_bytes > 0
      ? ((globals.disk_total_bytes - globals.disk_free_bytes) / globals.disk_total_bytes) * 100
      : 0;
  return (
    <section className="grid flex-shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
      <Kpi label="Tokens" value={globals ? formatIndianNumber(globals.tokens, 0) : "–"} />
      <Kpi
        label="Frames / sec"
        value={globals ? globals.fps.toFixed(2) : "–"}
        accent={fpsAccent(globals?.fps)}
        spark={fpsHistory}
      />
      <Kpi label="Captures" value={globals ? formatIndianNumber(globals.captures, 0) : "–"} />
      <Kpi
        label="Drop rate"
        value={globals ? formatPercent(globals.drop_rate_pct, 3) : "–"}
        accent={globals && globals.drop_rate_pct > 0 ? "bad" : "good"}
      />
      <Kpi
        label="Disk used"
        value={globals ? formatBytes(globals.disk_bytes) : "–"}
        sub={globals ? `${formatBytes(globals.disk_free_bytes)} free · ${diskPct.toFixed(0)}% used` : undefined}
      />
      <Kpi
        label="Frame loss (overall)"
        value={globals ? formatPercent(globals.frame_loss_pct, 2) : "–"}
        sub={globals ? `${formatIndianNumber(globals.frames_written, 0)} / ${formatIndianNumber(globals.frames_expected, 0)}` : undefined}
      />
    </section>
  );
}

function fpsAccent(fps: number | undefined): Accent {
  if (fps == null) return "none";
  if (fps >= 0.9 && fps <= 1.1) return "good";
  if (fps === 0) return "bad";
  return "warn";
}

type Accent = "none" | "good" | "warn" | "bad";

const ACCENT_TEXT: Record<Accent, string> = {
  none: "text-zinc-100",
  good: "text-green-400",
  warn: "text-amber-400",
  bad: "text-red-400",
};

function Kpi({
  label,
  value,
  sub,
  accent = "none",
  spark,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: Accent;
  spark?: number[];
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="flex items-end justify-between gap-2">
        <div className={`mt-0.5 text-xl font-bold tabular-nums ${ACCENT_TEXT[accent]}`}>{value}</div>
        {spark && spark.length > 1 && <Sparkline values={spark} accent={accent} />}
      </div>
      {sub && <div className="mt-0.5 truncate text-[10px] text-zinc-500">{sub}</div>}
    </div>
  );
}

const ACCENT_STROKE: Record<Accent, string> = {
  none: "#a1a1aa",
  good: "#22c55e",
  warn: "#f59e0b",
  bad: "#ef4444",
};

function Sparkline({ values, accent = "none" }: { values: number[]; accent?: Accent }) {
  const width = 56;
  const height = 18;
  const max = Math.max(...values, 0.0001);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} className="flex-shrink-0" aria-hidden="true">
      <polyline points={points} fill="none" stroke={ACCENT_STROKE[accent]} strokeWidth="1.5" />
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Frame integrity                                                            */
/* -------------------------------------------------------------------------- */

function FrameIntegrityPanel({
  rows,
  globals,
  expectedFrames,
}: {
  rows: PerUnderlyingStatus[];
  globals: GlobalStatus | null;
  expectedFrames: number;
}) {
  const overallCompleteness = globals ? 100 - globals.frame_loss_pct : 0;
  return (
    <Panel title="Frame integrity" subtitle={`baseline ${formatIndianNumber(expectedFrames, 0)} frames / session`}>
      <div className="mb-3 flex items-center gap-3">
        <Gauge value={overallCompleteness} />
        <div className="text-xs text-zinc-400">
          <div className="text-2xl font-bold tabular-nums text-zinc-100">
            {globals ? formatPercent(overallCompleteness, 1) : "–"}
          </div>
          <div>captured of full session</div>
        </div>
      </div>
      <div className="flex flex-col gap-1.5 overflow-auto">
        {rows.length === 0 ? (
          <Empty small message="Awaiting telemetry…" />
        ) : (
          rows.map((r) => {
            const completeness = Math.max(0, 100 - r.frame_loss_pct);
            return (
              <div key={r.underlying} className="text-xs">
                <div className="mb-0.5 flex justify-between">
                  <span className="font-medium text-zinc-300">{r.underlying}</span>
                  <span className="tabular-nums text-zinc-500">
                    {formatIndianNumber(r.frames_written, 0)} · loss {formatPercent(r.frame_loss_pct, 1)}
                  </span>
                </div>
                <Bar value={completeness} />
              </div>
            );
          })
        )}
      </div>
    </Panel>
  );
}

function Gauge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const color = pct >= 95 ? "#22c55e" : pct >= 50 ? "#f59e0b" : "#38bdf8";
  return (
    <div
      className="grid h-16 w-16 flex-shrink-0 place-items-center rounded-full"
      style={{ background: `conic-gradient(${color} ${pct * 3.6}deg, #27272a 0deg)` }}
    >
      <div className="grid h-12 w-12 place-items-center rounded-full bg-zinc-900 text-[10px] font-semibold text-zinc-300">
        {pct.toFixed(0)}%
      </div>
    </div>
  );
}

function Bar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const color = pct >= 95 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-sky-500";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
      <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Compression                                                                */
/* -------------------------------------------------------------------------- */

function CompressionPanel({
  current,
  history,
}: {
  current: CompressionProgressPayload | null;
  history: CompressionHistory | null;
}) {
  const phase = current?.phase ?? "idle";
  const pct =
    current && current.bytes_total > 0
      ? (current.bytes_done / current.bytes_total) * 100
      : phase === "done"
        ? 100
        : 0;
  return (
    <Panel title="Compression (EOD zstd)" subtitle={phase}>
      {current ? (
        <>
          <Bar value={pct} />
          <div className="mt-2 grid grid-cols-3 gap-2">
            <Stat label="Ratio" value={`${formatIndianNumber(current.ratio, 2)}×`} />
            <Stat label="Last batch" value={formatDuration(current.elapsed_ms)} />
            <Stat label="Throughput" value={formatThroughput(current.throughput_mbps)} />
            <Stat label="Files" value={`${current.files_done}/${current.files_total}`} />
            <Stat label="Avg / file" value={formatDuration(current.avg_file_ms)} />
            <Stat label="Threads" value={String(current.threads)} />
          </div>
          {current.current_file && (
            <div className="mt-1 truncate text-[10px] text-zinc-500">{current.current_file}</div>
          )}
        </>
      ) : (
        <Empty small message="No compression sweep yet today." />
      )}

      <div className="mt-3 border-t border-zinc-800 pt-2">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          Cross-day averages{history ? ` (${history.samples} sweeps)` : ""}
        </div>
        {history && history.samples > 0 ? (
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Avg ratio" value={`${formatIndianNumber(history.avg_ratio, 2)}×`} />
            <Stat label="Avg time" value={formatDuration(history.avg_total_elapsed_ms)} />
            <Stat label="Avg MB/s" value={formatThroughput(history.avg_throughput_mbps)} />
          </div>
        ) : (
          <div className="text-[11px] text-zinc-600">No history recorded yet.</div>
        )}
      </div>
    </Panel>
  );
}

/* -------------------------------------------------------------------------- */
/* Per-underlying table                                                       */
/* -------------------------------------------------------------------------- */

function PerUnderlyingPanel({ rows }: { rows: PerUnderlyingStatus[] }) {
  return (
    <Panel title="Per underlying" subtitle={`${rows.length} streams`}>
      {rows.length === 0 ? (
        <Empty message="Waiting for capture telemetry from the market-hours scheduler…" />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full text-left text-xs tabular-nums">
            <thead className="sticky top-0 bg-zinc-900 text-[10px] uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-2 py-1.5">Stream</th>
                <th className="px-2 py-1.5 text-right">Frames</th>
                <th className="px-2 py-1.5 text-right">Loss</th>
                <th className="px-2 py-1.5 text-right">B/frame</th>
                <th className="px-2 py-1.5 text-right">Proj EOD</th>
                <th className="px-2 py-1.5 text-right">File</th>
                <th className="px-2 py-1.5 text-right">Last tick</th>
                <th className="px-2 py-1.5 text-center">HB</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {rows.map((r) => (
                <tr key={r.underlying} className="hover:bg-zinc-800/40">
                  <td className="px-2 py-1.5">
                    <span className="flex items-center gap-1.5">
                      <span
                        className={`inline-block h-2 w-2 rounded-full ${
                          r.connected ? "bg-green-500" : "bg-red-500"
                        }`}
                      />
                      <span className="font-medium text-zinc-200">{r.underlying}</span>
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right">{formatIndianNumber(r.frames_written, 0)}</td>
                  <td className={`px-2 py-1.5 text-right ${r.frame_loss_pct > 50 ? "text-amber-400" : "text-zinc-300"}`}>
                    {formatPercent(r.frame_loss_pct, 1)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatBytes(r.avg_bytes_per_frame)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatBytes(r.projected_eod_bytes)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatBytes(r.file_bytes)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatClockTime(r.last_tick_ms)}</td>
                  <td className="px-2 py-1.5 text-center">
                    <span
                      className={`inline-block rounded-full px-1.5 py-0.5 text-[9px] ${
                        r.heartbeat_ok ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"
                      }`}
                      title={r.heartbeat_age_ms != null ? `${(r.heartbeat_age_ms / 1000).toFixed(1)}s ago` : "no data"}
                    >
                      {r.heartbeat_ok ? "1 Hz" : "stale"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/* -------------------------------------------------------------------------- */
/* Logs                                                                       */
/* -------------------------------------------------------------------------- */

function LogStrip({ logs, onExpand }: { logs: LogLine[]; onExpand: () => void }) {
  const recent = logs.slice(0, 3);
  return (
    <section className="flex-shrink-0 rounded-lg border border-zinc-800 bg-black/40">
      <div className="flex items-center justify-between border-b border-zinc-800/60 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">Session / log</span>
        <button onClick={onExpand} className="text-[11px] text-sky-400 hover:text-sky-300">
          expand ⤢
        </button>
      </div>
      <div className="h-[3.75rem] overflow-hidden px-3 py-1 font-mono text-[11px] leading-5">
        {recent.length === 0 ? (
          <span className="text-zinc-600">No session messages yet.</span>
        ) : (
          recent.map((line, i) => <LogRow key={`${line.ts}-${i}`} line={line} />)
        )}
      </div>
    </section>
  );
}

function FullLogs({ logs }: { logs: LogLine[] }) {
  return (
    <div className="h-full overflow-auto font-mono text-xs leading-5">
      {logs.length === 0 ? (
        <span className="text-zinc-600">No session messages yet.</span>
      ) : (
        logs.map((line, i) => <LogRow key={`${line.ts}-${i}`} line={line} />)
      )}
    </div>
  );
}

function LogRow({ line }: { line: LogLine }) {
  return (
    <div className="text-zinc-400">
      <span className="text-sky-400">{new Date(line.ts).toLocaleTimeString()}</span>{" "}
      <span className={line.kind === "session" ? "text-amber-400" : ""}>{line.text}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Download history (overlay)                                                 */
/* -------------------------------------------------------------------------- */

function HistoryPanel() {
  return (
    <Panel title="Download history" subtitle="live + archived captures">
      <div className="flex min-h-0 flex-1 flex-col">
        <HistoryTable />
      </div>
    </Panel>
  );
}

function HistoryTable() {
  const [history, setHistory] = useState<CaptureHistory | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const next = await getCaptureHistory();
        if (active) {
          setHistory(next.available ? next : null);
          setError(next.available ? null : "Capture history is unavailable until the backend is configured.");
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Capture history refresh failed.");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 15_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  if (history === undefined) return <Empty message="Loading capture history…" />;
  if (history === null) return <Empty message={error ?? "Capture history unavailable."} />;

  const archiveShare =
    history.totals.total_bytes > 0 ? (history.totals.archived_bytes / history.totals.total_bytes) * 100 : 0;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="grid flex-shrink-0 grid-cols-2 gap-2 sm:grid-cols-4">
        <Kpi label="Sessions" value={formatIndianNumber(history.totals.sessions, 0)} />
        <Kpi label="Data files" value={formatIndianNumber(history.totals.data_files, 0)} />
        <Kpi label="Stored data" value={formatBytes(history.totals.total_bytes)} />
        <Kpi label="Archived share" value={`${archiveShare.toFixed(1)}%`} />
      </div>
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800">
        <table className="w-full text-left text-xs tabular-nums">
          <thead className="sticky top-0 bg-zinc-950 text-[10px] uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-3 py-2">Session</th>
              <th className="px-3 py-2">State</th>
              <th className="px-3 py-2 text-right">Stored</th>
              <th className="px-3 py-2 text-right">Raw / archive</th>
              <th className="px-3 py-2 text-right">Files</th>
              <th className="px-3 py-2">Captured sets</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {history.sessions.map((s) => {
              const state =
                s.raw_files > 0 && s.archived_files > 0
                  ? "Archiving"
                  : s.raw_files > 0
                    ? s.is_current
                      ? "Recording"
                      : "Raw"
                    : "Archived";
              return (
                <tr key={s.trading_date} className="text-zinc-300">
                  <td className="whitespace-nowrap px-3 py-2 font-medium text-zinc-100">
                    {s.trading_date}
                    {s.is_current && (
                      <span className="ml-2 rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] text-sky-300">current</span>
                    )}
                  </td>
                  <td className="px-3 py-2">{state}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">{formatBytes(s.total_bytes)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right text-zinc-500">
                    {formatBytes(s.raw_bytes)} / {formatBytes(s.archived_bytes)}
                  </td>
                  <td className="px-3 py-2 text-right">{formatIndianNumber(s.data_files, 0)}</td>
                  <td className="px-3 py-2 text-zinc-400">
                    {s.indices.length > 0 ? s.indices.join(", ") : "No indices"}
                    {s.stock_files > 0 ? ` · stocks (${s.stock_files})` : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Shared primitives                                                          */
/* -------------------------------------------------------------------------- */

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
      <div className="mb-2 flex flex-shrink-0 items-baseline justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
        {subtitle && <span className="text-[10px] lowercase text-zinc-600">{subtitle}</span>}
      </div>
      {children}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-zinc-800/80 bg-zinc-950/40 px-2 py-1">
      <div className="text-[9px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="text-sm font-semibold tabular-nums text-zinc-100">{value}</div>
    </div>
  );
}

function Empty({ message, small = false }: { message: string; small?: boolean }) {
  return (
    <div
      className={`grid flex-1 place-items-center rounded-lg border border-dashed border-zinc-800 text-center text-zinc-500 ${
        small ? "p-3 text-[11px]" : "p-6 text-sm"
      }`}
    >
      {message}
    </div>
  );
}

function Overlay({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-6" onClick={onClose}>
      <div
        className="flex h-[80vh] w-full max-w-5xl flex-col rounded-xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex flex-shrink-0 items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
          <button onClick={onClose} className="rounded px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-800">
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
