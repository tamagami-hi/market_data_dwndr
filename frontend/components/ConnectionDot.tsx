"use client";

import type { TopicConnection } from "@/lib/wsTopicConnection";
import { useConnectionState } from "@/lib/useTopic";
import { formatBytes } from "@/lib/numberFormat";
import { Explanation } from "@/components/ui/Explanation";

/**
 * Live connection indicator with transport health.
 *
 * Shows three things, all of which are honest measurements:
 *   • dot        — socket connected / not; amber if no message for >5s
 *   • <n>ms      — SERVER-side build latency from `meta.pipeline_ms`: measured from just
 *                  before the first Greeks reconstruction until the whole 1 Hz batch is
 *                  encoded and ready to stream. Server-measured on purpose: comparing a
 *                  server timestamp against the browser clock would report clock skew.
 *   • <n>/s      — decompressed payload throughput measured in the browser over a
 *                  trailing window. The wire cost is lower (permessage-deflate is on,
 *                  ~3x on this JSON), but the browser only sees decompressed frames.
 *
 * NOTE on `showLatency`: the build latency covers the whole batch, so the SAME value is
 * stamped on every topic in that batch. Rendering it on two dots of one page would look
 * like two independent latencies when it is one measurement — so a page showing several
 * dots should enable it on one of them only. Throughput, by contrast, is genuinely
 * per-topic.
 *
 * Pass `detailed={false}` for a bare dot + label.
 */
export default function ConnectionDot({
  connection,
  label,
  detailed = true,
  showLatency = true,
}: {
  connection: TopicConnection;
  label: string;
  detailed?: boolean;
  showLatency?: boolean;
}) {
  const state = useConnectionState(connection);
  const stale = state.ageMs != null && state.ageMs > 5_000;
  const latencyMs = showLatency ? state.pipelineMs : null;
  const connectionLabel = state.connected ? (stale ? "stale" : "connected") : "offline";

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-secondary">
      <span
        className={`inline-block h-2 w-2 shrink-0 rounded-full ${
          state.connected
            ? stale
              ? "bg-warning"
              : "bg-success"
            : "bg-danger"
        }`}
        aria-hidden="true"
      />
      <span className="whitespace-nowrap">{label}: {connectionLabel}</span>
      {detailed && state.connected && (
        <span className="inline-flex items-center gap-1 whitespace-nowrap font-mono text-xs text-muted">
          <span className={latencyMs !== null && latencyMs > 500 ? "text-warning" : ""}>
            {latencyMs !== null ? `${latencyMs}ms` : ""}
            {latencyMs !== null && state.bytesPerSec > 0 ? " / " : ""}
            {state.bytesPerSec > 0 ? `${formatBytes(state.bytesPerSec)}/s` : ""}
          </span>
          <Explanation label={`Explain ${label} connection metrics`}>
            {showLatency && (
              <span className="block">
                Build latency: {state.pipelineMs ?? "--"}ms. Greeks: {state.greeksMs ?? "--"}ms.
                Stocks: {state.stocksMs ?? "--"}ms.
              </span>
            )}
            <span className="mt-1 block">
              Payload rate: {formatBytes(state.bytesPerSec)}/s decompressed. Last message:{" "}
              {state.ageMs === null ? "--" : `${(state.ageMs / 1000).toFixed(1)}s ago`}.
            </span>
          </Explanation>
        </span>
      )}
    </span>
  );
}
