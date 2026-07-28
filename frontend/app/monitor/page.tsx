"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import {
  getCaptureHistory,
  getStats,
  type CaptureHistory,
  type CompressionHistory,
  type DashboardStats,
  type RefreshWindow,
  type SessionSummary,
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
  kind: "log" | "session" | "alert";
}

const MAX_LOGS = 300;
const SPARK_SAMPLES = 60;
// Poll cadence: fast while the session is live or being established (capture running
// or the pre-open auth/token window). Outside those windows the numbers are final, so
// we back off — but only to 60s. A 5-minute idle interval meant a change on the server
// (e.g. a newly written snapshot) could take minutes to appear, which reads as the app
// being stuck. One small JSON request per minute is a negligible cost for that.
const POLL_ACTIVE_MS = 10_000;
const POLL_IDLE_MS = 60_000;

export default function MonitorPage() {
  const [rows, setRows] = useState<PerUnderlyingStatus[]>([]);
  const [globals, setGlobals] = useState<GlobalStatus | null>(null);
  const [compression, setCompression] = useState<CompressionProgressPayload | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [overlay, setOverlay] = useState<null | "logs">(null);
  const [fpsHistory, setFpsHistory] = useState<number[]>([]);
  // Remembers the last health state so we only log on *transitions*, not every tick.
  const healthRef = useRef<{ degraded: boolean; stale: boolean; reconnects: number }>({
    degraded: false,
    stale: false,
    reconnects: 0,
  });

  const pushLog = useCallback((text: string, kind: LogLine["kind"]) => {
    setLogs((prev) => [{ ts: Date.now(), text, kind }, ...prev].slice(0, MAX_LOGS));
  }, []);

  const onCaptureStatus = useCallback(
    (env: WsEnvelope) => {
      if (env.type === MSG.CAPTURE_STATUS) {
        const payload = env.payload as CaptureStatusPayload;
        setRows(payload.per_underlying ?? []);
        setGlobals(payload.global ?? null);
        if (payload.global) {
          const g = payload.global;
          setFpsHistory((prev) => [...prev, g.fps].slice(-SPARK_SAMPLES));

          // Surface live-feed health changes as newest-on-top session log entries.
          const prev = healthRef.current;
          if (g.stale && !prev.stale) {
            const secs = g.data_age_ms != null ? (g.data_age_ms / 1000).toFixed(1) : "?";
            pushLog(`⚠ live feed STALE — data unchanged for ${secs}s; reconnecting…`, "alert");
          } else if (!g.degraded && prev.degraded) {
            pushLog("✓ live feed recovered — fresh ticks resumed", "session");
          }
          if (g.reconnects > prev.reconnects) {
            const tier = g.reconnect_tier === 2 ? " (fresh token)" : "";
            pushLog(`↻ self-driven ticker reconnect (#${g.reconnects})${tier}`, "alert");
          }
          healthRef.current = {
            degraded: g.degraded,
            stale: g.stale,
            reconnects: g.reconnects,
          };
        }
      } else if (env.type === MSG.COMPRESSION_PROGRESS) {
        setCompression(env.payload as CompressionProgressPayload);
      }
    },
    [pushLog],
  );

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

  // Poll /api/stats for compression averages, session history, and the retained
  // snapshot. The interval adapts to the refresh window the backend reports, so we
  // only poll frequently while a session is live or being established (08:30–09:00).
  const captureRunning = stats?.capture_running ?? false;
  const shouldRefresh = stats?.refresh_window?.should_refresh ?? true;
  const pollMs = captureRunning || shouldRefresh ? POLL_ACTIVE_MS : POLL_IDLE_MS;

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const next = await getStats();
        if (!active) return;
        setStats(next);
        // Fall back to persisted / current compression when no live WS update yet.
        setCompression((cur) => cur ?? next.compression ?? null);
        // Retain data: when capture is not streaming, show the persisted snapshot —
        // which may be an EARLIER session — instead of leaving the page blank. While
        // capture IS running the WebSocket owns these values, so don't fight it.
        // Note this *replaces* on every idle poll: the previous version only filled
        // empty state, so a snapshot written after the page loaded never appeared.
        if (next.monitor && !next.capture_running) {
          setRows(next.monitor.per_underlying ?? []);
          setGlobals(next.monitor.global ?? null);
        }
      } catch {
        /* transient; keep last good */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), pollMs);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [pollMs]);

  const expectedFrames = stats?.expected_frames_per_session ?? 23_400;
  const history = stats?.compression_history ?? null;
  const tradingDate = stats?.trading_date ?? null;
  const persisted = Boolean(stats && !stats.capture_running && stats.monitor_persisted);
  // The session the displayed telemetry belongs to (may differ from today).
  const shownDate = stats?.monitor_trading_date ?? null;
  const isPastSession = Boolean(persisted && shownDate && shownDate !== tradingDate);
  const sessionHistory = stats?.session_history ?? [];
  const refreshWindow = stats?.refresh_window ?? null;

  return (
    // The dashboard flows naturally and the PAGE scrolls, at every breakpoint. It used
    // to be pinned to the viewport (h-[calc(100dvh-5.25rem)] + overflow-hidden), but
    // with three panel rows each scroll area collapsed to ~40px — unreadable.
    // Panels keep a minimum height instead, so nothing is ever crushed.
    //
    // On a TALL viewport we additionally set a min-height so the columns stretch and
    // fill the screen rather than leaving a large empty band below. It is a min-height,
    // not a fixed height with overflow-hidden, so content can still grow past it and the
    // page simply scrolls — that is what keeps the old squeeze from coming back. Gated
    // on viewport HEIGHT (not width), because that is what actually decides whether
    // there is spare vertical room.
    <div className="flex flex-col gap-2 text-zinc-200 [@media(min-height:1000px)]:min-h-[calc(100dvh-8rem)]">
      <TopBar
        globals={globals}
        tradingDate={tradingDate}
        persisted={persisted}
        shownDate={shownDate}
        isPastSession={isPastSession}
        refreshWindow={refreshWindow}
        captureRunning={captureRunning}
      />

      <KpiStrip globals={globals} fpsHistory={fpsHistory} />

      {/* ONE grid, not two independent columns. Previously each column was its own flex
          stack, so every panel sat at its own natural height and nothing lined up across
          the gutter: Frame integrity started part-way down Per underlying, Session
          history part-way down Download history, and the two columns ended at different
          depths. Grid auto-placement puts the panels on shared rows instead, so each
          pair sits on one baseline and each row is as tall as its taller panel.

          Children are therefore in ROW order (left, right, left, right, ...), which is
          also the order the single-column mobile layout reads in.

          On a tall viewport auto-rows-fr additionally makes the three rows split the
          spare height equally, so the dashboard fills the screen with even bands rather
          than leaving a ragged gap at the bottom. */}
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-[1fr_1.25fr] [@media(min-height:1000px)]:flex-1 [@media(min-height:1000px)]:lg:auto-rows-fr">
        <DataLossPanel globals={globals} />
        <PerUnderlyingPanel rows={rows} />
        <FrameIntegrityPanel rows={rows} globals={globals} expectedFrames={expectedFrames} />
        <SessionHistoryPanel sessions={sessionHistory} />
        <HistoryPanel />
        <CompressionPanel current={compression} history={history} />
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
  shownDate,
  isPastSession,
  refreshWindow,
  captureRunning,
}: {
  globals: GlobalStatus | null;
  tradingDate: string | null;
  persisted: boolean;
  shownDate: string | null;
  isPastSession: boolean;
  refreshWindow: RefreshWindow | null;
  captureRunning: boolean;
}) {
  const live = captureRunning;
  const refreshing = live || Boolean(refreshWindow?.should_refresh);
  return (
    <header className="flex flex-shrink-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 sm:gap-x-4 sm:px-4">
      <h1 className="text-sm font-semibold text-zinc-100 sm:text-base">Capture Monitor</h1>
      {tradingDate && (
        <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">{tradingDate}</span>
      )}
      <span className="text-xs text-zinc-500">
        uptime <span className="font-mono text-zinc-300">{formatUptime(globals?.uptime_ms)}</span>
      </span>
      {globals?.exhausted && (
        <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-xs font-semibold text-red-300">
          recovery exhausted — restart required
        </span>
      )}
      {globals?.ingestion_degraded && (
        <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-medium text-red-400">
          ingestion degraded
        </span>
      )}
      {globals?.stale && (
        <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-medium text-red-400">
          feed stale
          {globals.data_age_ms != null ? ` · ${(globals.data_age_ms / 1000).toFixed(0)}s` : ""}
        </span>
      )}
      {globals != null && !globals.stale && globals.reconnects > 0 && (
        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-400">
          {globals.reconnects} reconnect{globals.reconnects === 1 ? "" : "s"}
        </span>
      )}
      {isPastSession ? (
        <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-xs text-sky-300">
          showing last session · {shownDate}
        </span>
      ) : (
        persisted && (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-400">
            last saved snapshot (capture idle)
          </span>
        )
      )}
      <div className="flex w-full items-center gap-3 sm:ml-auto sm:w-auto sm:gap-4">
        <span
          className="text-[0.625rem] uppercase tracking-wide text-zinc-500"
          title={
            refreshWindow
              ? `auth window ${refreshWindow.auth_poll_start}–${refreshWindow.auth_poll_end} IST` +
                (refreshWindow.local_time ? ` · now ${refreshWindow.local_time}` : "")
              : undefined
          }
        >
          {live ? "live" : refreshing ? "refreshing" : "idle · retained"}
        </span>
        {/* Build latency is one server-wide measurement stamped on the whole batch, so
            it is shown on the capture dot only — repeating it on the session heartbeat
            dot would read as two independent latencies. Throughput stays per-topic. */}
        <ConnectionDot connection={captureStatusConnection} label="capture" />
        <ConnectionDot connection={sessionConnection} label="session" showLatency={false} />
      </div>
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* Data loss (per session)                                                    */
/* -------------------------------------------------------------------------- */

function DataLossPanel({ globals }: { globals: GlobalStatus | null }) {
  if (!globals) {
    return (
      <Panel title="Data loss (this session)" subtitle="grid-second accounting">
        <Empty small message="Awaiting telemetry…" />
      </Panel>
    );
  }
  const lost = globals.grid_seconds_lost ?? 0;
  const gaps = globals.grid_gaps ?? 0;
  const frozen = globals.frozen_seconds ?? 0;
  const sessionLoss = globals.session_loss_pct ?? 0;
  const sessionExpected = globals.session_frames_expected ?? 0;
  const runway = globals.disk_runway_hours ?? 0;
  return (
    <Panel
      title="Data loss (this session)"
      subtitle={`vs ${formatIndianNumber(sessionExpected, 0)} elapsed grid seconds`}
    >
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {/* Severity lives on the fields themselves. A separate red banner underneath was
            only restating "Seconds lost" and "Gap events", so the same number appeared
            twice in one panel. */}
        <Stat
          label="Seconds lost"
          value={formatIndianNumber(lost, 0)}
          tone={lost > 0 ? "bad" : "normal"}
          title={
            lost > 0
              ? `${formatIndianNumber(lost, 0)} grid second(s) permanently lost — these ` +
                `seconds were never written and cannot be recovered.`
              : "No grid seconds lost this session."
          }
        />
        <Stat
          label="Gap events"
          value={formatIndianNumber(gaps, 0)}
          tone={gaps > 0 ? "bad" : "normal"}
          title={
            gaps > 0
              ? `${formatIndianNumber(gaps, 0)} resync event(s): the capture fell far ` +
                `enough behind that whole grid seconds could not be written. A restart ` +
                `counts as one gap covering its downtime.`
              : "No resync events this session."
          }
        />
        <Stat
          label="Frozen secs"
          value={formatIndianNumber(frozen, 0)}
          tone={frozen > 0 ? "warn" : "normal"}
          title={
            frozen > 0
              ? `${formatIndianNumber(frozen, 0)} second(s) written while the feed was ` +
                `known stale, so they hold duplicate values. The frames exist — the ` +
                `contents just did not change.`
              : "No frozen seconds: every written second carried fresh data."
          }
        />
        <Stat
          label="Elapsed loss"
          value={formatPercent(sessionLoss, 3)}
          tone={sessionLoss >= 1 ? "bad" : sessionLoss > 0 ? "warn" : "normal"}
          title="Missing frames vs the grid seconds that have actually elapsed — the real health signal, unaffected by how much of the day remains."
        />
        <Stat label="Unmatched ticks" value={formatIndianNumber(globals.unmatched_ticks ?? 0, 0)} />
        <Stat label="Dropped batches" value={formatIndianNumber(globals.dropped_batches, 0)} />
        <Stat
          label="Ticks / sec"
          value={formatIndianNumber(globals.ticks_per_sec ?? 0, 1)}
          title="Current ingest rate over a trailing window (not a session average)."
        />
        <Stat label="Writer lag" value={formatIndianNumber(globals.writer_lag_max ?? 0, 0)} />
        <Stat
          label="Disk runway"
          value={runway > 0 ? `${formatIndianNumber(runway, 1)} h` : "–"}
        />
      </div>
    </Panel>
  );
}

/* -------------------------------------------------------------------------- */
/* Session history (cross-session data loss)                                  */
/* -------------------------------------------------------------------------- */

/**
 * Per-day capture quality, one row per trading date.
 *
 * Carries the same measures as the Data loss panel, but frozen at each session's end
 * instead of live — that panel goes blank the moment capture stops, so this table is the
 * only place a past day's health survives. Columns mirror the panel's order (lost / gaps
 * / frozen, then drops / unmatched / ticks) so the two read the same way, and share its
 * colour rules: red for anything permanently lost, amber for degraded but recoverable.
 */
function SessionHistoryPanel({ sessions }: { sessions: SessionSummary[] }) {
  return (
    <Panel title="Session history" subtitle={`${sessions.length} recorded sessions`}>
      {sessions.length === 0 ? (
        <Empty small message="No completed sessions recorded yet." />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full text-left text-xs tabular-nums">
            <thead className="sticky top-0 bg-zinc-900 text-[0.625rem] uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-1.5 py-1.5">Session</th>
                <th className="px-1.5 py-1.5 text-right" title="Grid seconds captured">
                  Frames
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Missing frames vs the grid seconds that actually elapsed"
                >
                  Loss
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Grid seconds permanently lost — never written, unrecoverable"
                >
                  Lost s
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Resync events that cost whole grid seconds"
                >
                  Gaps
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Seconds written while the feed was stale: frames exist but hold duplicate values"
                >
                  Frozen
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Tick batches dropped before reaching the grid"
                >
                  Drop
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Ticks that matched no instrument in the captured universe"
                >
                  Unmatch
                </th>
                <th
                  className="px-1.5 py-1.5 text-right"
                  title="Average ingest rate across the session (total ticks / uptime)"
                >
                  Ticks/s
                </th>
                <th className="px-1.5 py-1.5 text-right" title="Ticker reconnects">
                  Recon
                </th>
                <th className="px-1.5 py-1.5 text-right" title="Capture uptime for the session">
                  Uptime
                </th>
                <th className="px-1.5 py-1.5 text-right" title="Bytes on disk for the day">
                  Disk
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {sessions.map((s) => {
                const uptimeS = (s.uptime_ms ?? 0) / 1000;
                const avgTicks = uptimeS >= 1 ? (s.ticks_received ?? 0) / uptimeS : 0;
                return (
                  <tr key={s.trading_date} className="hover:bg-zinc-800/40">
                    <td className="whitespace-nowrap px-1.5 py-1.5 font-medium text-zinc-200">
                      {s.trading_date}
                      {s.exhausted && (
                        <span
                          className="ml-1.5 rounded-full bg-red-500/15 px-1.5 py-0.5 text-[0.5625rem] text-red-400"
                          title="Reconnect attempts were exhausted — the session stopped early"
                        >
                          stalled
                        </span>
                      )}
                    </td>
                    <td className="px-1.5 py-1.5 text-right text-zinc-300">
                      {formatIndianNumber(s.captures, 0)}
                    </td>
                    <Num value={s.session_loss_pct} tone={s.session_loss_pct > 1 ? "warn" : "none"}>
                      {formatPercent(s.session_loss_pct, 2)}
                    </Num>
                    <Num value={s.grid_seconds_lost} tone="bad" />
                    <Num value={s.grid_gaps} tone="bad" />
                    <Num value={s.frozen_seconds} tone="warn" />
                    <Num value={s.dropped_batches} tone="bad" />
                    <Num value={s.unmatched_ticks} tone="warn" />
                    <td className="px-1.5 py-1.5 text-right text-zinc-400">
                      {formatIndianNumber(avgTicks, 0)}
                    </td>
                    <Num value={s.reconnects} tone="warn" />
                    <td className="whitespace-nowrap px-1.5 py-1.5 text-right text-zinc-400">
                      {formatUptime(s.uptime_ms)}
                    </td>
                    <td className="whitespace-nowrap px-1.5 py-1.5 text-right text-zinc-400">
                      {formatBytes(s.disk_bytes ?? 0)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/**
 * Numeric history cell. Zero stays muted and only a non-zero value takes the severity
 * colour, so a clean session reads as a quiet row and real problems stand out.
 */
function Num({
  value,
  tone,
  children,
}: {
  value: number;
  tone: "bad" | "warn" | "none";
  children?: React.ReactNode;
}) {
  const active = value > 0 && tone !== "none";
  const cls = active ? (tone === "bad" ? "text-red-400" : "text-amber-400") : "text-zinc-500";
  return (
    <td className={`px-1.5 py-1.5 text-right ${cls}`}>
      {children ?? formatIndianNumber(value, 0)}
    </td>
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
        value={globals ? (globals.fps ?? 0).toFixed(2) : "–"}
        accent={fpsAccent(globals?.fps)}
        spark={fpsHistory}
        sub={
          globals
            ? `writer lag ${globals.writer_lag_max ?? 0} · build ${(globals.snapshot_ms ?? 0).toFixed(1)}ms`
            : undefined
        }
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
      <div className="text-[0.625rem] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="flex items-end justify-between gap-2">
        <div className={`mt-0.5 text-xl font-bold tabular-nums ${ACCENT_TEXT[accent]}`}>{value}</div>
        {spark && spark.length > 1 && <Sparkline values={spark} accent={accent} />}
      </div>
      {sub && <div className="mt-0.5 truncate text-[0.625rem] text-zinc-500">{sub}</div>}
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
      {/* Compact header row: smaller gauge + inline figure, so the per-stream list
          below keeps real height instead of being squeezed to a couple of pixels. */}
      <div className="mb-2 flex flex-shrink-0 items-center gap-2.5">
        <Gauge value={overallCompleteness} />
        <div className="text-[0.625rem] leading-tight text-zinc-500">
          <div className="text-lg font-bold tabular-nums text-zinc-100">
            {globals ? formatPercent(overallCompleteness, 1) : "–"}
          </div>
          captured of full session
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="flex flex-col gap-1.5">
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
      </div>
    </Panel>
  );
}

function Gauge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const color = pct >= 95 ? "#22c55e" : pct >= 50 ? "#f59e0b" : "#38bdf8";
  return (
    <div
      className="grid h-12 w-12 flex-shrink-0 place-items-center rounded-full"
      style={{ background: `conic-gradient(${color} ${pct * 3.6}deg, #27272a 0deg)` }}
    >
      <div className="grid h-9 w-9 place-items-center rounded-full bg-zinc-900 text-[0.5625rem] font-semibold text-zinc-300">
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
          {/* Compact 3x2 micro-stat grid: labels and values on one line each so the
              panel stops dominating the column. */}
          <div className="mt-1.5 grid grid-cols-3 gap-x-2 gap-y-1">
            <MicroStat label="Ratio" value={`${formatIndianNumber(current.ratio, 2)}×`} />
            <MicroStat label="Elapsed" value={formatDuration(current.elapsed_ms)} />
            <MicroStat label="MB/s" value={formatThroughput(current.throughput_mbps)} />
            <MicroStat label="Files" value={`${current.files_done}/${current.files_total}`} />
            <MicroStat label="Avg/file" value={formatDuration(current.avg_file_ms)} />
            <MicroStat label="Threads" value={String(current.threads)} />
          </div>
          {current.current_file && (
            <div className="mt-1 truncate text-[0.625rem] text-zinc-500">{current.current_file}</div>
          )}
        </>
      ) : (
        <Empty small message="No compression sweep yet today." />
      )}

      {/* Cross-day averages as a single inline row instead of three boxed stats. */}
      <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-t border-zinc-800 pt-1.5 text-[0.625rem] text-zinc-500">
        <span className="uppercase tracking-wide">
          avg{history ? ` · ${history.samples} sweeps` : ""}
        </span>
        {history && history.samples > 0 ? (
          <>
            <span className="tabular-nums text-zinc-300">
              {formatIndianNumber(history.avg_ratio, 2)}× ratio
            </span>
            <span className="tabular-nums text-zinc-300">
              {formatDuration(history.avg_total_elapsed_ms)}
            </span>
            <span className="tabular-nums text-zinc-300">
              {formatThroughput(history.avg_throughput_mbps)}
            </span>
          </>
        ) : (
          <span className="text-zinc-600">no history yet</span>
        )}
      </div>
    </Panel>
  );
}

/** Label + value on one line — much denser than the boxed `Stat`. */
function MicroStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-1 border-b border-zinc-800/60 pb-0.5">
      <span className="truncate text-[0.5625rem] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className="shrink-0 text-[0.6875rem] font-semibold tabular-nums text-zinc-100">{value}</span>
    </div>
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
            <thead className="sticky top-0 bg-zinc-900 text-[0.625rem] uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-2 py-1.5">Stream</th>
                <th className="px-2 py-1.5 text-right">Frames</th>
                <th
                  className="px-2 py-1.5 text-right"
                  title="Missing frames vs the grid seconds that have ACTUALLY elapsed — the real health signal. Above ~0 means genuine data loss."
                >
                  Loss
                </th>
                <th
                  className="px-2 py-1.5 text-right"
                  title="Share of the full session captured so far. Low early in the day by definition — progress, not a fault."
                >
                  Day
                </th>
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
                  {/* Elapsed-based loss: the real health signal. The old column divided by
                      the whole-day baseline, so a perfect 10:30 session read ~75% "loss". */}
                  <td
                    className={`px-2 py-1.5 text-right ${
                      (r.session_loss_pct ?? 0) > 0.5 ? "text-amber-400" : "text-zinc-300"
                    }`}
                    title="vs grid seconds actually elapsed"
                  >
                    {formatPercent(r.session_loss_pct ?? 0, 2)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-500" title="share of the full session captured">
                    {formatPercent(r.day_complete_pct ?? 100 - r.frame_loss_pct, 1)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatBytes(r.avg_bytes_per_frame)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatBytes(r.projected_eod_bytes)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatBytes(r.file_bytes)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">{formatClockTime(r.last_tick_ms)}</td>
                  <td className="px-2 py-1.5 text-center">
                    <span
                      className={`inline-block rounded-full px-1.5 py-0.5 text-[0.5625rem] ${
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
        <span className="text-[0.625rem] uppercase tracking-wide text-zinc-500">Session / log</span>
        <button onClick={onExpand} className="text-[0.6875rem] text-sky-400 hover:text-sky-300">
          expand ⤢
        </button>
      </div>
      <div className="h-[3.75rem] overflow-hidden px-3 py-1 font-mono text-[0.6875rem] leading-5">
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
  const textClass =
    line.kind === "alert" ? "text-red-400" : line.kind === "session" ? "text-amber-400" : "";
  return (
    <div className="text-zinc-400">
      <span className="text-sky-400">{new Date(line.ts).toLocaleTimeString()}</span>{" "}
      <span className={textClass}>{line.text}</span>
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
    <div className="flex h-full min-h-0 flex-col gap-1.5">
      {/* Totals as one dense inline strip instead of four tall Kpi cards — that row
          was eating the height the table needs to show more than one session. */}
      <div className="flex flex-shrink-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded border border-zinc-800/80 bg-zinc-950/40 px-2 py-1 text-[0.625rem] text-zinc-500">
        <Inline label="sessions" value={formatIndianNumber(history.totals.sessions, 0)} />
        <Inline label="files" value={formatIndianNumber(history.totals.data_files, 0)} />
        <Inline label="stored" value={formatBytes(history.totals.total_bytes)} />
        <Inline label="archived" value={`${archiveShare.toFixed(1)}%`} />
      </div>
      <div className="min-h-[9rem] flex-1 overflow-auto rounded-lg border border-zinc-800">
        <table className="w-full text-left text-[0.6875rem] tabular-nums">
          <thead className="sticky top-0 bg-zinc-950 text-[0.5625rem] uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-2 py-1">Session</th>
              <th className="px-2 py-1">State</th>
              <th className="px-2 py-1 text-right">Stored</th>
              <th className="hidden px-2 py-1 text-right sm:table-cell">Raw / archive</th>
              <th className="px-2 py-1 text-right">Files</th>
              <th className="hidden px-2 py-1 lg:table-cell">Captured sets</th>
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
                  <td className="whitespace-nowrap px-2 py-1 font-medium text-zinc-100">
                    {s.trading_date}
                    {s.is_current && (
                      <span className="ml-1.5 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[0.5625rem] text-sky-300">
                        current
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1">{state}</td>
                  <td className="whitespace-nowrap px-2 py-1 text-right">{formatBytes(s.total_bytes)}</td>
                  <td className="hidden whitespace-nowrap px-2 py-1 text-right text-zinc-500 sm:table-cell">
                    {formatBytes(s.raw_bytes)} / {formatBytes(s.archived_bytes)}
                  </td>
                  <td className="px-2 py-1 text-right">{formatIndianNumber(s.data_files, 0)}</td>
                  <td className="hidden px-2 py-1 text-zinc-400 lg:table-cell">
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

/** `label value` on one line, for dense summary strips. */
function Inline({ label, value }: { label: string; value: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="uppercase tracking-wide">{label} </span>
      <span className="font-semibold tabular-nums text-zinc-200">{value}</span>
    </span>
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
    // A real minimum height at every breakpoint: the panel's scroll regions must stay
    // tall enough to show a header plus several rows, which is what broke when the
    // dashboard was pinned to the viewport. The panel is a grid item, so it stretches to
    // its row's height automatically (h-full makes that explicit) — that is what keeps
    // the two panels in a row aligned even when one has more content than the other.
    <section className="flex h-full min-h-[14rem] flex-col rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
      <div className="mb-2 flex flex-shrink-0 flex-wrap items-baseline justify-between gap-x-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
        {subtitle && <span className="text-[0.625rem] lowercase text-zinc-600">{subtitle}</span>}
      </div>
      {children}
    </section>
  );
}

/**
 * One metric tile. `tone` carries severity on the value itself, so a panel can signal a
 * problem in place instead of appending a separate coloured banner that repeats the same
 * number.
 */
function Stat({
  label,
  value,
  tone = "normal",
  title,
}: {
  label: string;
  value: string;
  tone?: "normal" | "warn" | "bad";
  title?: string;
}) {
  const toneClass =
    tone === "bad" ? "text-red-400" : tone === "warn" ? "text-amber-400" : "text-zinc-100";
  const borderClass =
    tone === "bad"
      ? "border-red-500/40 bg-red-500/5"
      : tone === "warn"
        ? "border-amber-500/40 bg-amber-500/5"
        : "border-zinc-800/80 bg-zinc-950/40";
  return (
    <div className={`rounded border px-2 py-1 ${borderClass}`} title={title}>
      <div className="text-[0.5625rem] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${toneClass}`}>{value}</div>
    </div>
  );
}

function Empty({ message, small = false }: { message: string; small?: boolean }) {
  return (
    <div
      className={`grid flex-1 place-items-center rounded-lg border border-dashed border-zinc-800 text-center text-zinc-500 ${
        small ? "p-3 text-[0.6875rem]" : "p-6 text-sm"
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
