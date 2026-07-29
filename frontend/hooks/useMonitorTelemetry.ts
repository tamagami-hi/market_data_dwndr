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
import { captureStatusConnection } from "@/lib/wsTopicConnection";
import {
  MSG,
  type CompressionProgressPayload,
  type GlobalStatus,
  type PerUnderlyingStatus,
  type WsEnvelope,
} from "@/lib/wsTypes";

const MAX_FPS_SAMPLES = 60;

/** Rolling per-second samples backing the KPI sparklines. */
export interface KpiSeries {
  tokens: number[];
  drop: number[];
  loss: number[];
}
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
  const [fpsHistory, setFpsHistory] = useState<number[]>([]);
  // Rolling samples for the KPI sparklines. Kept client-side and capped like fpsHistory:
  // the backend already sends these fields every second, so persisting a series server
  // side would duplicate data the page is receiving anyway.
  const [kpiSeries, setKpiSeries] = useState<KpiSeries>({ tokens: [], drop: [], loss: [] });
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [captureHistory, setCaptureHistory] = useState<CaptureHistory | null>(null);
  const [restError, setRestError] = useState<string | null>(null);
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);
  const [clockNow, setClockNow] = useState(0);
  const [hasLiveCaptureTelemetry, setHasLiveCaptureTelemetry] = useState(false);
  const pollMsRef = useRef(ACTIVE_POLL_MS);
  const captureRunningRef = useRef<boolean | null>(null);
  const compressionWsAtRef = useRef<number | null>(null);

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
    setKpiSeries((current) => ({
      tokens: [...current.tokens, payload.global.tokens].slice(-MAX_FPS_SAMPLES),
      drop: [...current.drop, payload.global.drop_rate_pct].slice(-MAX_FPS_SAMPLES),
      loss: [...current.loss, payload.global.session_loss_pct ?? 0].slice(-MAX_FPS_SAMPLES),
    }));

  }, []);

  useTopicEnvelopes(captureStatusConnection, onCaptureStatus);

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
    live: { rows, globals, compression, fpsHistory, kpiSeries },
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
