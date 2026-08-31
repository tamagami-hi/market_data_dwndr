"use client";

/**
 * Topic-aware WebSocket connection factory (ported from algo_engine's
 * wsTopicConnection, trimmed for the capture-only tagged-envelope protocol).
 *
 * Each topic gets one ref-counted WebSocket with automatic reconnect/backoff.
 * Messages are parsed as JSON envelopes ({ type, payload }).
 */

import { getBackendWsUrl } from "@/lib/config";
import type { WsEnvelope } from "@/lib/wsTypes";

export interface WsConnectionState {
  connected: boolean;
  error: string | null;
  /**
   * Server-side build latency (ms): from just before the first Greeks reconstruction
   * until the 1 Hz batch is encoded and ready to stream.
   */
  pipelineMs: number | null;
  /** Portion of pipelineMs spent on IV/Greeks. */
  greeksMs: number | null;
  /** Portion spent building the columnar stock board. */
  stocksMs: number | null;
  /** Decompressed bytes received on this topic over the trailing window, per second. */
  bytesPerSec: number;
  /** Milliseconds since the last message arrived (client clock only — no skew). */
  ageMs: number | null;
}

export interface TopicConnection {
  acquire(): void;
  release(): void;
  onEnvelope(handler: (envelope: WsEnvelope) => void): () => void;
  subscribeState(listener: () => void): () => void;
  getState(): WsConnectionState;
}

const MAX_RETRIES = 10;
const RECOVERY_DELAY_MS = 60_000;
// Throughput is averaged over a trailing window so the number is readable rather than
// spiking with each 1 Hz burst.
/** Trailing window for the throughput average. Exported so the telemetry popover can
 *  state the real value instead of a hardcoded copy that could drift. */
export const RATE_WINDOW_MS = 5_000;

/**
 * Silence after which the watchdog force-closes an *open* socket so the existing
 * onclose/backoff path reconnects it.
 *
 * Deliberately much longer than the 5 s cosmetic "stale" threshold used by the connection
 * dot: brief jitter must not cause reconnect churn. This exists for the case a TCP/
 * WebSocket connection stays technically open while the server has stopped delivering —
 * the client used to sit at `connected: true` with frozen numbers indefinitely, because
 * reconnect was only ever triggered by onclose/onerror.
 */
export const WATCHDOG_SILENCE_MS = 12_000;

function parseEnvelope(raw: string): WsEnvelope | null {
  try {
    const parsed = JSON.parse(raw) as { type?: unknown; payload?: unknown; meta?: unknown };
    if (!parsed || typeof parsed !== "object" || typeof parsed.type !== "string") {
      return null;
    }
    return { type: parsed.type, payload: parsed.payload, meta: parsed.meta as WsEnvelope["meta"] };
  } catch {
    return null;
  }
}

function createTopicConnection(
  topic: string,
  options: { watchdog?: boolean } = {},
): TopicConnection {
  // Only topics that are *expected* to deliver continuously may be watchdogged. An
  // event-driven topic (e.g. historical-jobs) is legitimately silent for hours, and
  // force-closing it would be pure churn.
  const watchdogEnabled = options.watchdog === true;
  let connState: WsConnectionState = {
    connected: false,
    error: null,
    pipelineMs: null,
    greeksMs: null,
    stocksMs: null,
    bytesPerSec: 0,
    ageMs: null,
  };
  let ws: WebSocket | null = null;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let retries = 0;
  let intentionalClose = false;
  let refCount = 0;
  // (arrivalMs, byteLength) samples inside the trailing window.
  let samples: Array<[number, number]> = [];
  let lastMessageAt: number | null = null;
  let statsTimer: ReturnType<typeof setInterval> | null = null;
  /**
   * Armed by each arriving message and cleared when the watchdog fires, so a silence
   * episode costs at most one forced reconnect. Without this, a topic that is quiet for a
   * legitimate reason (market closed) would be closed and reopened every
   * WATCHDOG_SILENCE_MS forever, since a successful open resets the retry counter.
   */
  let watchdogArmed = false;

  const stateListeners = new Set<() => void>();
  const envelopeHandlers = new Set<(envelope: WsEnvelope) => void>();

  function emitState(): void {
    stateListeners.forEach((l) => l());
  }
  function setConnState(next: Partial<WsConnectionState>): void {
    connState = { ...connState, ...next };
    emitState();
  }

  /** Recompute the trailing throughput + age. Cheap; called on a timer, not per message. */
  function refreshStats(): void {
    const now = Date.now();
    samples = samples.filter(([t]) => now - t <= RATE_WINDOW_MS);
    const bytes = samples.reduce((sum, [, n]) => sum + n, 0);
    const spanMs = samples.length > 0 ? Math.max(RATE_WINDOW_MS, now - samples[0][0]) : 0;
    setConnState({
      bytesPerSec: spanMs > 0 ? Math.round((bytes / spanMs) * 1000) : 0,
      ageMs: lastMessageAt == null ? null : now - lastMessageAt,
    });
    maybeTripWatchdog(now);
  }

  /**
   * Force-close a socket that is open but has gone silent, letting the existing
   * onclose/backoff logic reconnect. Handles the server-stopped-delivering case, which
   * produces no close and no error and so was previously never recovered from.
   */
  function maybeTripWatchdog(now: number): void {
    if (!watchdogEnabled || !watchdogArmed) return;
    if (lastMessageAt == null) return;
    if (now - lastMessageAt < WATCHDOG_SILENCE_MS) return;
    const socket = ws;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    watchdogArmed = false; // one forced reconnect per silence episode
    console.warn(
      `[ws:${topic}] no message for ${now - lastMessageAt}ms on an open socket; ` +
        "closing to force a reconnect",
    );
    // NOT an intentional close: onclose must run the reconnect path.
    socket.close();
  }
  function clearReconnect(): void {
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
  }

  function connect(): void {
    if (ws || refCount === 0) return;
    intentionalClose = false;
    try {
      const url = `${getBackendWsUrl()}/ws/${topic}`;
      const socket = new WebSocket(url);
      ws = socket;

      socket.onopen = () => {
        retries = 0;
        clearReconnect();
        setConnState({ connected: true, error: null });
      };

      socket.onmessage = (event) => {
        const raw = event.data as string;
        // Measure on the CLIENT: this is the payload that actually arrived, and it needs
        // no clock comparison with the server. (permessage-deflate means fewer bytes on
        // the wire than this — the browser exposes only the decompressed frame.)
        lastMessageAt = Date.now();
        samples.push([lastMessageAt, typeof raw === "string" ? raw.length : 0]);
        watchdogArmed = true; // live data: re-arm for the next silence episode

        if (envelopeHandlers.size === 0) return;
        const envelope = parseEnvelope(raw);
        if (!envelope) return;
        const m = envelope.meta;
        if (m && typeof m.pipeline_ms === "number" && m.pipeline_ms !== connState.pipelineMs) {
          setConnState({
            pipelineMs: m.pipeline_ms,
            greeksMs: typeof m.greeks_ms === "number" ? m.greeks_ms : connState.greeksMs,
            stocksMs: typeof m.stocks_ms === "number" ? m.stocks_ms : connState.stocksMs,
          });
        }
        for (const handler of envelopeHandlers) {
          try {
            handler(envelope);
          } catch (err) {
            console.error(`[ws:${topic}] handler threw:`, err);
          }
        }
      };

      socket.onerror = () => {
        if (intentionalClose || refCount === 0) return;
        setConnState({ connected: false });
      };

      socket.onclose = () => {
        if (ws === socket) ws = null;
        if (intentionalClose || refCount === 0) {
          intentionalClose = false;
          return;
        }
        setConnState({ connected: false });
        if (retries < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retries, 30_000);
          retries += 1;
          reconnectTimeout = setTimeout(() => {
            reconnectTimeout = null;
            connect();
          }, delay);
        } else {
          setConnState({ error: `Connection lost on /ws/${topic} — retrying in 60s.` });
          retries = 0;
          reconnectTimeout = setTimeout(() => {
            reconnectTimeout = null;
            connect();
          }, RECOVERY_DELAY_MS);
        }
      };
    } catch {
      setConnState({ connected: false });
    }
  }

  function disconnect(): void {
    intentionalClose = true;
    clearReconnect();
    if (statsTimer) {
      clearInterval(statsTimer);
      statsTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
    samples = [];
    lastMessageAt = null;
    watchdogArmed = false;
    connState = {
      connected: false, error: null, pipelineMs: null, greeksMs: null,
      stocksMs: null, bytesPerSec: 0, ageMs: null,
    };
  }

  return {
    acquire() {
      refCount += 1;
      if (refCount === 1) {
        if (statsTimer === null) statsTimer = setInterval(refreshStats, 1000);
        connect();
      }
    },
    release() {
      refCount = Math.max(0, refCount - 1);
      if (refCount === 0) disconnect();
    },
    onEnvelope(handler) {
      envelopeHandlers.add(handler);
      return () => {
        envelopeHandlers.delete(handler);
      };
    },
    subscribeState(listener) {
      stateListeners.add(listener);
      return () => {
        stateListeners.delete(listener);
      };
    },
    getState() {
      return connState;
    },
  };
}

export const marketDataConnection = createTopicConnection("market-data", { watchdog: true });
export const stocksConnection = createTopicConnection("stocks", { watchdog: true });
export const captureStatusConnection = createTopicConnection("capture-status", {
  watchdog: true,
});
export const sessionConnection = createTopicConnection("session", { watchdog: true });
// Event-driven: silent for hours between jobs, so no watchdog.
export const historicalJobsConnection = createTopicConnection("historical-jobs");

/** Exported for tests: build an isolated connection without touching the singletons. */
export const __createTopicConnectionForTest = createTopicConnection;
