import type { Severity } from "@/lib/monitor/severity";

const STATUS_CLASS: Record<Severity, string> = {
  neutral: "status-neutral",
  success: "status-success",
  warning: "status-warning",
  danger: "status-danger",
};

export function StatusIndicator({
  label,
  severity,
  compact = false,
}: {
  label: string;
  severity: Severity;
  compact?: boolean;
}) {
  return (
    <span className={`status-indicator ${STATUS_CLASS[severity]} ${compact ? "status-compact" : ""}`}>
      <span aria-hidden="true" className="status-dot" />
      {label}
    </span>
  );
}
