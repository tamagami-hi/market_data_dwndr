import { StatusIndicator } from "@/components/ui/StatusIndicator";
import type { SessionSummary } from "@/lib/api";
import {
  OUTCOME_LABEL,
  OUTCOME_SEVERITY,
  primaryCompleteness,
  specialSessionLabel,
} from "@/lib/monitor/sessionOutcome";
import { formatPercent } from "@/lib/numberFormat";

/** Lines shown inline; the rest stay one click away in Session history. */
const MAX_LINES = 4;

/**
 * The backend's verdict on the day the panel shows (``/api/stats.session_outcome``):
 * outcome class, primary completeness and its plain-language explanation. While the day
 * runs the verdict is provisional (measured against the elapsed schedule).
 */
export function SessionOutcomeStrip({ outcome }: { outcome: SessionSummary | null }) {
  if (!outcome?.outcome) return null;
  const lines = outcome.explanation ?? [];
  const shown = lines.slice(0, MAX_LINES);
  const hidden = lines.length - shown.length;
  const special = specialSessionLabel(outcome.session_window);
  return (
    <div className="border-b border-border px-3 py-2 text-xs" aria-label="Session outcome">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusIndicator
          compact
          label={OUTCOME_LABEL[outcome.outcome]}
          severity={OUTCOME_SEVERITY[outcome.outcome]}
        />
        <span className="text-muted">
          <span className="font-mono text-primary">{formatPercent(primaryCompleteness(outcome), 2)}</span>{" "}
          captured{outcome.final === false ? " so far" : ""}
        </span>
        {special && <span className="text-warning">{special}</span>}
      </div>
      {shown.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-secondary" aria-label="Why">
          {shown.map((line, index) => (
            <li key={`${index}-${line}`}>{line}</li>
          ))}
          {hidden > 0 && <li className="text-muted">+{hidden} more in Session history</li>}
        </ul>
      )}
    </div>
  );
}
