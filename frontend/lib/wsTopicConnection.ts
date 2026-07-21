"use client";

/**
 * Topic-aware WebSocket connection factory (ported from algo_engine's
 * wsTopicConnection, trimmed for the capture-only tagged-envelope protocol).
 *
 * Each topic gets one ref-counted WebSocket with automatic reconnect/backoff.
 * Messages are parsed as JSON envelopes ({ type, payload }).
 */

import { getAuthToken, getBackendWsUrl } from "@/lib/config";
import type { WsEnvelope } from "@/lib/wsTypes";

export interface WsConnectionState {
  connected: boolean;
  error: string | null;
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

function parseEnvelope(raw: string): WsEnvelope | null {
  try {
    const parsed = JSON.parse(raw) as { type?: unknown; payload?: unknown };
    if (!parsed || typeof parsed !== "object" || typeof parsed.type !== "string") {
      return null;
    }
    return { type: parsed.type, payload: parsed.payload };
  } catch {
    return null;
  }
}

function createTopicConnection(topic: string): TopicConnection {
  let connState: WsConnectionState = { connected: false, error: null };
  let ws: WebSocket | null = null;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let retries = 0;
  let intentionalClose = false;
  let refCount = 0;

  const stateListeners = new Set<() => void>();
  const envelopeHandlers = new Set<(envelope: WsEnvelope) => void>();

  function emitState(): void {
    stateListeners.forEach((l) => l());
  }
  function setConnState(next: Partial<WsConnectionState>): void {
    connState = { ...connState, ...next };
    emitState();
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
      const url = `${getBackendWsUrl()}/ws/${topic}?token=${encodeURIComponent(getAuthToken())}`;
      const socket = new WebSocket(url);
      ws = socket;

      socket.onopen = () => {
        retries = 0;
        clearReconnect();
        setConnState({ connected: true, error: null });
      };

      socket.onmessage = (event) => {
        if (envelopeHandlers.size === 0) return;
        const envelope = parseEnvelope(event.data as string);
        if (!envelope) return;
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
    if (ws) {
      ws.close();
      ws = null;
    }
    connState = { connected: false, error: null };
  }

  return {
    acquire() {
      refCount += 1;
      if (refCount === 1) connect();
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
