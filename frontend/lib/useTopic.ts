"use client";

import { useEffect, useSyncExternalStore } from "react";

import type { TopicConnection, WsConnectionState } from "@/lib/wsTopicConnection";
import type { WsEnvelope } from "@/lib/wsTypes";

/** Acquire a topic connection for the component's lifetime and receive envelopes. */
export function useTopicEnvelopes(
  connection: TopicConnection,
  onEnvelope: (envelope: WsEnvelope) => void,
): void {
  useEffect(() => {
    connection.acquire();
    const unsub = connection.onEnvelope(onEnvelope);
    return () => {
      unsub();
      connection.release();
    };
    // onEnvelope is expected to be stable (useCallback) at call sites.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection]);
}

const SERVER_STATE: WsConnectionState = { connected: false, error: null };

/** Reactive connection state for a topic (SSR-safe). */
export function useConnectionState(connection: TopicConnection): WsConnectionState {
  return useSyncExternalStore(
    (listener) => connection.subscribeState(listener),
    () => connection.getState(),
    () => SERVER_STATE,
  );
}
