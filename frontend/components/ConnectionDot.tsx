"use client";

import { RATE_WINDOW_MS, type TopicConnection } from "@/lib/wsTopicConnection";
import { useConnectionState } from "@/lib/useTopic";
import { formatBytes, formatDuration } from "@/lib/numberFormat";
import { Explanation } from "@/components/ui/Explanation";

type ConnectionState = ReturnType<typeof useConnectionState>;
export type TelemetryProfile = "all" | "option" | "stocks" | "transport";

function connectionLabel(state: ConnectionState): string {
  if (!state.connected) return "offline";
  return state.ageMs != null && state.ageMs > 5_000 ? "stale" : "connected";
}

export function ConnectionDetails({
  label,
  state,
  profile = "all",
}: {
  label: string;
  state: ConnectionState;
  profile?: TelemetryProfile;
}) {
  const hasPipeline = profile !== "transport";
  const hasGreeks = profile === "all" || profile === "option";
  const hasStocks = profile === "all" || profile === "stocks";
  return (
    <section className="connection-detail">
      <h3 className="connection-detail-title">{label}</h3>
      <dl className="connection-detail-grid">
        <dt>State</dt>
        <dd>{connectionLabel(state)}</dd>
        {hasPipeline && (
          <>
            <dt>Pipeline build</dt>
            <dd>{formatDuration(state.pipelineMs)}</dd>
          </>
        )}
        {hasGreeks && (
          <>
            <dt>Greeks</dt>
            <dd>{formatDuration(state.greeksMs)}</dd>
          </>
        )}
        {hasStocks && (
          <>
            <dt>Stock board</dt>
            <dd>{formatDuration(state.stocksMs)}</dd>
          </>
        )}
        <dt>Payload</dt>
        <dd>{formatBytes(state.bytesPerSec)}/s</dd>
        <dt>Last message</dt>
        <dd>{state.ageMs === null ? "--" : `${formatDuration(state.ageMs)} ago`}</dd>
        <dt>Rate window</dt>
        <dd>{formatDuration(RATE_WINDOW_MS)}</dd>
        <dt>Payload basis</dt>
        <dd>decompressed</dd>
        {state.error && (
          <>
            <dt>Error</dt>
            <dd className="text-danger">{state.error}</dd>
          </>
        )}
      </dl>
    </section>
  );
}

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
  telemetryProfile = "all",
  telemetryTitle,
  popoverClassName = "",
}: {
  connection: TopicConnection;
  label: string;
  detailed?: boolean;
  showLatency?: boolean;
  telemetryProfile?: TelemetryProfile;
  telemetryTitle?: string;
  popoverClassName?: string;
}) {
  const state = useConnectionState(connection);
  const stale = state.ageMs != null && state.ageMs > 5_000;
  const latencyMs = showLatency ? state.pipelineMs : null;
  const stateLabel = connectionLabel(state);

  return (
    <div className="inline-flex items-center gap-1.5 text-xs text-secondary">
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
      <span className="whitespace-nowrap">{label}: {stateLabel}</span>
      {detailed && (
        <div className="inline-flex items-center gap-1 whitespace-nowrap font-mono text-xs text-muted">
          {state.connected && (
            <span className={latencyMs !== null && latencyMs > 500 ? "text-warning" : ""}>
              {latencyMs !== null ? `${latencyMs}ms` : ""}
              {latencyMs !== null && state.bytesPerSec > 0 ? " / " : ""}
              {state.bytesPerSec > 0 ? `${formatBytes(state.bytesPerSec)}/s` : ""}
            </span>
          )}
          <Explanation
            label={`Explain ${label} connection metrics`}
            contentClassName={popoverClassName}
          >
            <ConnectionDetails
              label={telemetryTitle ?? `${label} stream`}
              state={state}
              profile={showLatency ? telemetryProfile : "transport"}
            />
          </Explanation>
        </div>
      )}
    </div>
  );
}
