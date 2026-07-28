"use client";

import type { TopicConnection } from "@/lib/wsTopicConnection";
import { useConnectionState } from "@/lib/useTopic";
import { formatBytes } from "@/lib/numberFormat";

/**
 * Live connection indicator with transport health.
 *
 * Shows three things, all of which are honest measurements:
 *   • dot        — socket connected / not
 *   • <n>ms      — SERVER-side pipeline latency (grid timestamp -> encoded message),
 *                  taken from `meta.pipeline_ms`. Server-measured on purpose: comparing
 *                  a server timestamp against the browser clock would report clock skew.
 *   • <n>/s      — decompressed payload throughput measured in the browser over a
 *                  trailing window. The wire cost is lower (permessage-deflate is on,
 *                  ~3x on this JSON), but the browser only sees decompressed frames.
 *
 * Pass `detailed={false}` for a bare dot + label.
 */
export default function ConnectionDot({
  connection,
  label,
  detailed = true,
}: {
  connection: TopicConnection;
  label: string;
  detailed?: boolean;
}) {
  const state = useConnectionState(connection);
  const stale = state.ageMs != null && state.ageMs > 5_000;

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
      <span
        className={`inline-block h-2 w-2 shrink-0 rounded-full ${
          state.connected
            ? stale
              ? "bg-amber-500 shadow-[0_0_6px] shadow-amber-500"
              : "bg-green-500 shadow-[0_0_6px] shadow-green-500"
            : "bg-red-500"
        }`}
      />
      <span className="whitespace-nowrap">{label}</span>
      {detailed && state.connected && (
        <span
          className="whitespace-nowrap font-mono text-[0.6875rem] text-zinc-500"
          title={
            `build latency: ${state.pipelineMs ?? "–"} ms — measured from just before the first\n` +
            `Greeks reconstruction until the 1s batch is encoded and ready to stream.\n` +
            (state.greeksMs != null ? `  · IV/Greeks (all chains): ${state.greeksMs} ms\n` : "") +
            (state.stocksMs != null ? `  · stock board (L1–L5):   ${state.stocksMs} ms\n` : "") +
            `payload rate: ${formatBytes(state.bytesPerSec)}/s decompressed ` +
            `(~1/3 that on the wire — permessage-deflate)\n` +
            `last message: ${state.ageMs == null ? "–" : `${(state.ageMs / 1000).toFixed(1)}s ago`}`
          }
        >
          {state.pipelineMs != null && (
            <span className={state.pipelineMs > 500 ? "text-amber-400" : ""}>
              {state.pipelineMs}ms
            </span>
          )}
          {state.bytesPerSec > 0 && (
            <>
              {state.pipelineMs != null && <span className="text-zinc-700"> · </span>}
              {formatBytes(state.bytesPerSec)}/s
            </>
          )}
        </span>
      )}
    </span>
  );
}
