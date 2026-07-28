import { memo } from "react";

import { Metric } from "@/components/ui/Metric";
import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import type { CompressionHistory } from "@/lib/api";
import { formatDuration, formatIndianNumber, formatThroughput } from "@/lib/numberFormat";
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
  return (
    <Panel title="Compression" subtitle={current?.phase ?? "idle"} className="h-full">
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
            <Metric label="Ratio" value={`${formatIndianNumber(current.ratio, 2)}x`} />
            <Metric label="Throughput" value={formatThroughput(current.throughput_mbps)} />
            <Metric label="Elapsed" value={formatDuration(current.elapsed_ms)} />
            <Metric label="Average / file" value={formatDuration(current.avg_file_ms)} />
            <Metric label="Threads" value={String(current.threads)} />
            <Metric label="Sweeps" value={String(history?.samples ?? 0)} />
          </div>
          {current.current_file && <p className="break-all font-mono text-xs text-muted">{current.current_file}</p>}
        </div>
      ) : (
        <div className="p-3">
          <StateMessage title="No compression activity">The end-of-day sweep has not reported today.</StateMessage>
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
