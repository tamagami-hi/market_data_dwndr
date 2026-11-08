import type { LossCause } from "@/lib/monitor/lossCauses";
import { formatSecondsCompact } from "@/lib/numberFormat";

/** "stale feed 54s, process restart 1m 38s, not running 50m 32s" */
export function describeLossCauses(causes: readonly LossCause[]): string {
  return causes
    .filter((cause) => cause.seconds > 0)
    .map((cause) => `${cause.label} ${formatSecondsCompact(cause.seconds)}`)
    .join(", ");
}

/**
 * One stacked bar per session: each segment is a cause, sized by the seconds it cost, so
 * a crash-loop outage and a stale feed are told apart at a glance. The segments are
 * scaled to the missing total, so they fill the bar exactly when the record reconciles.
 * The accessible name and tooltip name every cause with its seconds.
 */
export function LossCauseBar({
  causes,
  missingSeconds,
}: {
  causes: readonly LossCause[];
  missingSeconds: number;
}) {
  const active = causes.filter((cause) => cause.seconds > 0);
  const attributed = active.reduce((sum, cause) => sum + cause.seconds, 0);
  const total = Math.max(missingSeconds, attributed);
  if (total <= 0 || active.length === 0) return null;
  const description = `Missing ${formatSecondsCompact(missingSeconds)}: ${describeLossCauses(active)}`;
  return (
    <div
      className="mt-1 flex h-1.5 w-full min-w-12 overflow-hidden rounded-full bg-[var(--surface-3)]"
      role="img"
      aria-label={description}
      title={description}
      data-loss-cause-bar
    >
      {active.map((cause) => (
        <span
          key={cause.key}
          data-cause={cause.key}
          className="h-full shrink-0"
          style={{
            width: `${(cause.seconds / total) * 100}%`,
            // A 54-second stale spell beside a 50-minute outage is under 2% of the bar;
            // keep it visible rather than rounding it away.
            minWidth: "2px",
            backgroundColor: cause.color,
          }}
        />
      ))}
    </div>
  );
}

/** Colour key for the bar; with ``showSeconds`` it doubles as the per-cause breakdown. */
export function LossCauseLegend({
  causes,
  showSeconds = true,
  label = "Missing-time causes",
}: {
  causes: readonly LossCause[];
  showSeconds?: boolean;
  label?: string;
}) {
  if (causes.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" aria-label={label}>
      {causes.map((cause) => (
        <li key={cause.key} className="inline-flex items-center gap-1">
          <span
            aria-hidden="true"
            className="inline-block size-2 shrink-0 rounded-sm"
            style={{ backgroundColor: cause.color }}
          />
          {cause.label}
          {showSeconds && (
            <span className="font-mono text-secondary">{formatSecondsCompact(cause.seconds)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}
