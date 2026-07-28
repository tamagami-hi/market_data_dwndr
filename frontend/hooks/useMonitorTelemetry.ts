"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { createPollController } from "@/hooks/polling";
import {
  getCaptureHistory,
  getStats,
  type CaptureHistory,
  type DashboardStats,
} from "@/lib/api";
import {
  deriveMonitorView,
  normalizeCaptureHistory,
  normalizeCaptureStatus,
  normalizeCompressionProgress,
  normalizeDashboardStats,
} from "@/lib/monitor/viewModel";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { captureStatusConnection, sessionConnection } from "@/lib/wsTopicConnection";
import {
  MSG,
  type CompressionProgressPayload,
  type GlobalStatus,
  type PerUnderlyingStatus,
  type WsEnvelope,
} from "@/lib/wsTypes";

export interface MonitorLogLine {
  id: number;
  ts: number;
  text: string;
  kind: "log" | "session" | "alert";
}

const MAX_LOGS = 300;
const MAX_FPS_SAMPLES = 60;
const ACTIVE_POLL_MS = 10_000;
const IDLE_POLL_MS = 60_000;
const ERROR_POLL_MS = 30_000;
const REQUEST_TIMEOUT_MS = 15_000;
const COMPRESSION_WS_FRESH_MS = 20_000;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function sameValue<T>(left: T | null, right: T): boolean {
  return left !== null && JSON.stringify(left) === JSON.stringify(right);
}

export function useMonitorTelemetry() {
  const [rows, setRows] = useState<PerUnderlyingStatus[]>([]);
  const [globals, setGlobals] = useState<GlobalStatus | null>(null);
  const [compression, setCompression] = useState<CompressionProgressPayload | null>(null);
  const [logs, setLogs] = useState<MonitorLogLine[]>([]);
  const [fpsHistory, setFpsHistory] = useState<number[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [captureHistory, setCaptureHistory] = useState<CaptureHistory | null>(null);
  const [restError, setRestError] = useState<string | null>(null);
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);
  const [clockNow, setClockNow] = useState(0);
  const [hasLiveCaptureTelemetry, setHasLiveCaptureTelemetry] = useState(false);
  const logIdRef = useRef(0);
  const pollMsRef = useRef(ACTIVE_POLL_MS);
  const healthRef = useRef({ degraded: false, stale: false, reconnects: 0 });
  const captureRunningRef = useRef<boolean | null>(null);
  const compressionWsAtRef = useRef<number | null>(null);

  const pushLog = useCallback((text: string, kind: MonitorLogLine["kind"]) => {
    const line = { id: ++logIdRef.current, ts: Date.now(), text, kind };
    setLogs((current) => [line, ...current].slice(0, MAX_LOGS));
  }, []);

  const onCaptureStatus = useCallback((envelope: WsEnvelope) => {
    if (envelope.type === MSG.COMPRESSION_PROGRESS) {
      const nextCompression = normalizeCompressionProgress(envelope.payload);
      if (nextCompression) {
        compressionWsAtRef.current = Date.now();
        setCompression(nextCompression);
      }
      else setPayloadError("A compression update had an invalid shape.");
      return;
    }
    if (envelope.type !== MSG.CAPTURE_STATUS) return;

    const payload = normalizeCaptureStatus(envelope.payload);
    if (!payload) {
      setPayloadError("A capture update had an invalid shape. Showing the last valid telemetry.");
      return;
    }
    setPayloadError(null);
    setHasLiveCaptureTelemetry(true);
    setRows(payload.per_underlying);
    setGlobals(payload.global);
    setFpsHistory((current) => [...current, payload.global.fps].slice(-MAX_FPS_SAMPLES));

    const previous = healthRef.current;
    const next = payload.global;
    if (next.stale && !previous.stale) {
      const seconds = next.data_age_ms === null ? "unknown" : (next.data_age_ms / 1000).toFixed(1);
      pushLog(`Live feed stale. Data unchanged for ${seconds}s.`, "alert");
    } else if (!next.degraded && previous.degraded) {
      pushLog("Live feed recovered. Fresh ticks resumed.", "session");
    }
    if (next.reconnects > previous.reconnects) {
      const tokenNote = next.reconnect_tier === 2 ? " with a fresh token" : "";
      pushLog(`Ticker reconnect ${next.reconnects}${tokenNote}.`, "alert");
    }
    healthRef.current = {
      degraded: next.degraded,
      stale: next.stale,
      reconnects: next.reconnects,
    };
  }, [pushLog]);

  const onSession = useCallback((envelope: WsEnvelope) => {
    const payload = envelope.payload;
    if (envelope.type === MSG.LOG) {
      const message =
        payload && typeof payload === "object" && "message" in payload && typeof payload.message === "string"
          ? payload.message
          : "";
      if (message) pushLog(message, "log");
    }
    if (envelope.type === MSG.SESSION_STATUS) {
      const phase =
        payload && typeof payload === "object" && "phase" in payload && typeof payload.phase === "string"
          ? payload.phase
          : "unknown";
      pushLog(`Session: ${phase}`, "session");
    }
  }, [pushLog]);

  useTopicEnvelopes(captureStatusConnection, onCaptureStatus);
  useTopicEnvelopes(sessionConnection, onSession);

  useEffect(() => {
    const controller = createPollController({
      intervalMs: () => pollMsRef.current,
      errorIntervalMs: () => ERROR_POLL_MS,
      timeoutMs: REQUEST_TIMEOUT_MS,
      onTimeout: () => {
        setRestError("Telemetry refresh timed out. Showing the last valid data.");
        setClockNow(Date.now());
      },
      isPaused: () => document.hidden,
      task: async (signal) => {
        const [statsResult, historyResult] = await Promise.allSettled([
          getStats(signal),
          getCaptureHistory(signal),
        ]);
        if (signal.aborted) return;

        const errors: string[] = [];
        let hasPollFailure = false;
        if (statsResult.status === "fulfilled") {
          const nextStats = normalizeDashboardStats(statsResult.value);
          if (!nextStats) {
            errors.push("Stats response was malformed.");
            hasPollFailure = true;
          } else {
            pollMsRef.current =
              nextStats.capture_running || nextStats.refresh_window?.should_refresh
                ? ACTIVE_POLL_MS
                : IDLE_POLL_MS;
            setStats((current) => sameValue(current, nextStats) ? current : nextStats);
            const compressionWsAge = compressionWsAtRef.current === null
              ? Number.POSITIVE_INFINITY
              : Date.now() - compressionWsAtRef.current;
            if (compressionWsAge >= COMPRESSION_WS_FRESH_MS) {
              setCompression(nextStats.compression);
            }
            if (captureRunningRef.current === false && nextStats.capture_running) {
              setHasLiveCaptureTelemetry(false);
              setRows([]);
              setGlobals(null);
            }
            if (!nextStats.capture_running && nextStats.monitor) {
              setHasLiveCaptureTelemetry(false);
              setRows(nextStats.monitor.per_underlying);
              setGlobals(nextStats.monitor.global);
            }
            if (!nextStats.capture_running && !nextStats.monitor) {
              setHasLiveCaptureTelemetry(false);
              setRows([]);
              setGlobals(null);
            }
            captureRunningRef.current = nextStats.capture_running;
            setLastSuccessAt(Date.now());
          }
        } else if (!(statsResult.reason instanceof DOMException && statsResult.reason.name === "AbortError")) {
          errors.push(errorMessage(statsResult.reason, "Stats refresh failed."));
          hasPollFailure = true;
        }

        if (historyResult.status === "fulfilled") {
          const nextHistory = normalizeCaptureHistory(historyResult.value);
          if (!nextHistory) {
            errors.push("Capture history response was malformed.");
            hasPollFailure = true;
          } else if (nextHistory.available) {
            setCaptureHistory((current) =>
              sameValue(current, nextHistory) ? current : nextHistory,
            );
          } else {
            setCaptureHistory(null);
            errors.push("Capture history is unavailable until the backend is configured.");
          }
        } else if (!(historyResult.reason instanceof DOMException && historyResult.reason.name === "AbortError")) {
          errors.push(errorMessage(historyResult.reason, "Capture history refresh failed."));
          hasPollFailure = true;
        }
        setRestError(errors.length > 0 ? [...new Set(errors)].join(" ") : null);
        setClockNow(Date.now());
        if (hasPollFailure) throw new Error("Telemetry polling failed.");
      },
    });

    const handleVisibility = () => {
      if (!document.hidden) controller.resume();
    };
    controller.start();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      controller.stop();
    };
  }, []);

  const view = useMemo(
    () =>
      deriveMonitorView({
        stats,
        hasLiveTelemetry: Boolean(stats?.capture_running && hasLiveCaptureTelemetry),
        lastSuccessAt,
        now: clockNow,
        restError,
      }),
    [clockNow, hasLiveCaptureTelemetry, lastSuccessAt, restError, stats],
  );

  return {
    live: { rows, globals, compression, logs, fpsHistory },
    history: {
      sessions: stats?.session_history ?? [],
      capture: captureHistory,
      compression: stats?.compression_history ?? null,
    },
    context: {
      captureRunning: stats?.capture_running ?? false,
      tradingDate: stats?.trading_date ?? null,
      shownDate: stats?.monitor_trading_date ?? null,
      refreshWindow: stats?.refresh_window ?? null,
      expectedFrames: stats?.expected_frames_per_session ?? 23_400,
    },
    freshness: { lastSuccessAt, restError, payloadError, isRestStale: view.isRestStale },
    source: { type: view.source, isPastSession: view.isPastSession },
  };
}
