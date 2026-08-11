"use client";

import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import { progressColor } from "@/lib/monitor/progressColor";
import { formatIndianNumber, formatPercent } from "@/lib/numberFormat";
import type { GlobalStatus, PerUnderlyingStatus } from "@/lib/wsTypes";

/**
 * Day-completeness against the full-session baseline: a gauge for the whole capture
 * plus one bar per stream.
 *
 * Restored from the v0.1.26 dashboard, which paired this with Session history in the
 * middle row. It answers a different question from Per-underlying health (which reports
 * live connection state) and from Data-loss diagnostics (which measures elapsed time):
 * how much of a FULL trading session is on disk so far.
 *
 * Colours come from the current design tokens, so only the layout is inherited.
 */

function Gauge({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const filled = (clamped / 100) * circumference;
  const stroke = progressColor(clamped);
  return (
    <svg viewBox="0 0 44 44" className="h-11 w-11 shrink-0" aria-hidden="true">
      <circle cx="22" cy="22" r={radius} fill="none" stroke="var(--border)" strokeWidth="4" />
      <circle
        cx="22"
        cy="22"
        r={radius}
        fill="none"
        stroke={stroke}
        data-progress-gauge
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference - filled}`}
        transform="rotate(-90 22 22)"
      />
      <text
        x="22"
        y="22"
        textAnchor="middle"
        dominantBaseline="central"
        className="fill-[var(--text-muted)] font-mono"
        style={{ fontSize: "9px" }}
      >
        {Math.round(clamped)}%
      </text>
    </svg>
  );
}

function Bar({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  const fill = progressColor(clamped);
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
      <div
        data-progress-bar
        className="integrity-progress h-full rounded-full"
        style={{ width: `${clamped}%`, backgroundColor: fill }}
      />
    </div>
  );
}

export function FrameIntegrity({
  rows,
  globals,
  expectedFrames,
}: {
  rows: PerUnderlyingStatus[];
  globals: GlobalStatus | null;
  expectedFrames: number;
}) {
  const overall = globals ? Math.max(0, 100 - globals.frame_loss_pct) : 0;

  return (
    <Panel
      title="Frame integrity"
      subtitle={`baseline ${formatIndianNumber(expectedFrames, 0)} frames / session`}
    >
      {/* Compact gauge row so the per-stream list below keeps real height rather than
          being squeezed to a few pixels. */}
      <div className="flex shrink-0 items-center gap-2.5 px-3 pt-2">
        <Gauge value={overall} />
        <div className="text-xs leading-tight text-muted">
          <div className="font-mono text-lg font-semibold tabular-nums text-primary">
            {globals ? formatPercent(overall, 1) : "--"}
          </div>
          captured of full session
          {/* The full-session figure is a progress bar for the day and reads high even
              when the feed is dead (a frozen morning still leaves the afternoon intact).
              The elapsed real-data share is the number that exposes that. */}
          {globals && (globals.data_loss_pct ?? 0) > 0 && (
            <div className="mt-0.5 text-warning">
              {formatPercent(Math.max(0, 100 - (globals.data_loss_pct ?? 0)), 1)} real data of
              elapsed
            </div>
          )}
        </div>
      </div>
      <div className="monitor-integrity-scroll min-h-0 flex-1 overflow-auto p-3">
        {rows.length === 0 ? (
          <StateMessage title="Awaiting telemetry">
            Per-stream completeness appears once capture reports status.
          </StateMessage>
        ) : (
          <div className="flex flex-col gap-1.5">
            {rows.map((row) => {
              const completeness = Math.max(0, 100 - row.frame_loss_pct);
              return (
                <div key={row.underlying} className="text-xs">
                  <div className="mb-0.5 flex justify-between gap-2">
                    <span className="truncate font-medium text-secondary">{row.underlying}</span>
                    <span className="shrink-0 font-mono tabular-nums text-muted">
                      {formatIndianNumber(row.frames_written, 0)} · remaining{" "}
                      {formatPercent(row.frame_loss_pct, 1)}
                    </span>
                  </div>
                  <Bar value={completeness} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Panel>
  );
}
