import { memo } from "react";

import { Metric } from "@/components/ui/Metric";
import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import type { CompressionHistory } from "@/lib/api";
import { formatBytes, formatDuration, formatIndianNumber, formatThroughput } from "@/lib/numberFormat";
import type { CompressionProgressPayload } from "@/lib/wsTypes";

export const CompressionActivity = memo(function CompressionActivity({
  current,
  history,
}: {
  current: CompressionProgressPayload | null;
  history: CompressionHistory | null;
}) {
  const percent =
    current && current.bytes_total > 0
      ? Math.min(100, (current.bytes_done / current.bytes_total) * 100)
      : current?.phase === "done"
        ? 100
        : 0;

  // `current` is live automation state held in backend memory: it is null on a fresh day
  // and after any backend restart, which is why this panel used to go blank and claim the
  // sweep "has not reported today" even though a sweep had completed. The last sweep is
  // persisted in compression-history.jsonl and already returned by the API as
  // `history.last`, so fall back to it and label it as retained rather than live.
  const retained = !current ? (history?.last ?? null) : null;

  return (
    <Panel
      title="Compression"
      subtitle={current?.phase ?? (retained ? `last sweep · ${retained.trading_date}` : "idle")}
      className="panel-compact h-full"
    >
      {current ? (
        <div className="space-y-2 p-2">
          <div className="flex items-center justify-between text-xs text-secondary">
            <span>{current.files_done} / {current.files_total} files</span>
            <span className="font-mono">{percent.toFixed(0)}%</span>
          </div>
          <div className="h-1 overflow-hidden rounded bg-surface-3" aria-hidden="true">
            <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Metric compact label="Ratio" value={`${formatIndianNumber(current.ratio, 2)}x`} />
            <Metric compact label="Throughput" value={formatThroughput(current.throughput_mbps)} />
            <Metric compact label="Elapsed" value={formatDuration(current.elapsed_ms)} />
            <Metric compact label="Average / file" value={formatDuration(current.avg_file_ms)} />
            <Metric compact label="Threads" value={String(current.threads)} />
            <Metric compact label="Sweeps" value={String(history?.samples ?? 0)} />
          </div>
          {current.current_file && <p className="break-all font-mono text-xs text-muted">{current.current_file}</p>}
        </div>
      ) : retained ? (
        <div className="space-y-2 p-2">
          <div className="flex items-center justify-between text-xs text-secondary">
            <span>{formatIndianNumber(retained.files, 0)} files compressed</span>
            <span className="font-mono text-muted">retained</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Metric compact label="Ratio" value={`${formatIndianNumber(retained.ratio, 2)}x`} />
            <Metric compact label="Throughput" value={formatThroughput(retained.throughput_mbps)} />
            <Metric compact label="Elapsed" value={formatDuration(retained.total_elapsed_ms)} />
            <Metric compact label="Average / file" value={formatDuration(retained.avg_file_ms)} />
            <Metric compact label="Threads" value={retained.threads == null ? "--" : String(retained.threads)} />
            <Metric compact label="Sweeps" value={String(history?.samples ?? 0)} />
          </div>
          <p className="font-mono text-xs text-muted">
            {formatBytes(retained.raw_bytes)} raw {"->"} {formatBytes(retained.zst_bytes)} zstd
          </p>
        </div>
      ) : (
        <div className="p-3">
          <StateMessage title="No compression activity">
            No end-of-day sweep has been recorded yet.
          </StateMessage>
        </div>
      )}
      {history && history.samples > 0 && (
        <div className="border-t border-border px-3 py-2 text-xs text-muted">
          Average {formatIndianNumber(history.avg_ratio, 2)}x / {formatDuration(history.avg_total_elapsed_ms)} /{" "}
          {formatThroughput(history.avg_throughput_mbps)}
        </div>
      )}
    </Panel>
  );
});
