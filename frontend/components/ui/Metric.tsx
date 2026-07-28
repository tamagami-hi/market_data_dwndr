import type { ReactNode } from "react";

import type { Severity } from "@/lib/monitor/severity";

const TONE_CLASS: Record<Severity, string> = {
  neutral: "text-primary",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};

export function Metric({
  label,
  value,
  detail,
  severity = "neutral",
  accessory,
  compact = false,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  severity?: Severity;
  accessory?: ReactNode;
  /** Dense dashboard strips (see .metric-compact) — smaller chrome, same content. */
  compact?: boolean;
}) {
  return (
    <div className={compact ? "metric metric-compact" : "metric"}>
      <span className="label text-muted">{label}</span>
      <div className={`flex items-end justify-between gap-2 ${compact ? "mt-0.5" : "mt-1"}`}>
        <span
          className={`font-mono font-semibold tabular-nums ${compact ? "text-base" : "text-lg"} ${TONE_CLASS[severity]}`}
        >
          {value}
        </span>
        {accessory}
      </div>
      {detail && <div className="mt-1 text-xs text-muted">{detail}</div>}
    </div>
  );
}
