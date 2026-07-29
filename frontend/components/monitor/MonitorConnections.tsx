"use client";

import { useEffect } from "react";

import { ConnectionDetails } from "@/components/ConnectionDot";
import { Explanation } from "@/components/ui/Explanation";
import { useConnectionState } from "@/lib/useTopic";
import type { TopicConnection } from "@/lib/wsTopicConnection";
import {
  captureStatusConnection,
  marketDataConnection,
  sessionConnection,
  stocksConnection,
} from "@/lib/wsTopicConnection";

const STALE_AFTER_MS = 5_000;

type State = ReturnType<typeof useConnectionState>;

function useObservedState(connection: TopicConnection): State {
  const state = useConnectionState(connection);
  useEffect(() => {
    connection.acquire();
    const unsubscribe = connection.onEnvelope(() => {});
    return () => {
      unsubscribe();
      connection.release();
    };
  }, [connection]);
  return state;
}

function stateTone(states: State[]): { label: string; tone: string } {
  if (states.some((state) => !state.connected)) {
    return { label: "offline", tone: "bg-danger" };
  }
  if (states.some((state) => state.ageMs != null && state.ageMs > STALE_AFTER_MS)) {
    return { label: "stale", tone: "bg-warning" };
  }
  return { label: "connected", tone: "bg-success" };
}

export function MonitorConnections() {
  const capture = useConnectionState(captureStatusConnection);
  const options = useObservedState(marketDataConnection);
  const stocks = useObservedState(stocksConnection);
  const session = useConnectionState(sessionConnection);
  const collective = stateTone([capture, session]);

  return (
    <div className="connection-summary">
      <span className={`h-2 w-2 shrink-0 rounded-full ${collective.tone}`} aria-hidden="true" />
      <span className="whitespace-nowrap">capture + session: {collective.label}</span>
      <Explanation
        label="Explain monitor connections"
        contentClassName="monitor-connection-popover"
      >
        <h2 className="mb-3 text-sm font-semibold text-primary">Monitor telemetry</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <ConnectionDetails label="Capture Monitor" state={capture} profile="transport" />
          <ConnectionDetails label="Option Chain" state={options} profile="option" />
          <ConnectionDetails label="Stocks Board" state={stocks} profile="stocks" />
          <ConnectionDetails label="Session" state={session} profile="transport" />
        </div>
        <p className="mt-3 border-t border-border pt-2 text-muted">
          Frozen seconds are separate from elapsed missing-frame loss: frozen snapshots
          were written on time but contained stale feed values.
        </p>
      </Explanation>
    </div>
  );
}
