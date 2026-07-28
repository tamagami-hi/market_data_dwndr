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
  /** Server-side latency of the last message: grid timestamp -> encoded (ms). */
  pipelineMs: number | null;
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
const RATE_WINDOW_MS = 5_000;

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

function createTopicConnection(topic: string): TopicConnection {
  let connState: WsConnectionState = {
    connected: false,
    error: null,
    pipelineMs: null,
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

        if (envelopeHandlers.size === 0) return;
        const envelope = parseEnvelope(raw);
        if (!envelope) return;
        const pm = envelope.meta?.pipeline_ms;
        if (typeof pm === "number" && pm !== connState.pipelineMs) {
          setConnState({ pipelineMs: pm });
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
    connState = { connected: false, error: null, pipelineMs: null, bytesPerSec: 0, ageMs: null };
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

export const marketDataConnection = createTopicConnection("market-data");
export const stocksConnection = createTopicConnection("stocks");
export const captureStatusConnection = createTopicConnection("capture-status");
export const sessionConnection = createTopicConnection("session");
export const historicalJobsConnection = createTopicConnection("historical-jobs");
