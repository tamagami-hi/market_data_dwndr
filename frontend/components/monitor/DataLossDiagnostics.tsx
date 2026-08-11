import { Metric } from "@/components/ui/Metric";
import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import { formatIndianNumber, formatPercent } from "@/lib/numberFormat";
import { lossSeverity } from "@/lib/monitor/severity";
import type { GlobalStatus } from "@/lib/wsTypes";

/**
 * Three numbers answer three different questions, and conflating them is what let a
 * dead feed pass as a clean session:
 *
 *   Elapsed loss — of the seconds we COULD have written, how many did we miss?
 *                  (gaps and write-path failures only)
 *   Stale seconds — seconds the feed had nothing new, so no frame was written at all.
 *   Data loss    — of every second of the session, how much is missing from the archive?
 *                  Gaps + stale. This is the honest headline figure.
 */
/**
 * A live stale spell, badged while it is happening.
 *
 * The 2026-08-04/05/06 sessions each lost 9-91 minutes of market data to a feed that had
 * silently stopped delivering ticks, and nobody could see it until the end-of-day session
 * history. A spell in progress is now stated plainly, and the three states read
 * differently because they mean different things:
 *
 *   disarmed  — no ticks yet but the exchange is not trading; entirely normal.
 *   armed     — the feed is dead mid-session; the process will restart itself over it.
 *   abandoned — the day's restart budget is spent and the feed never came back.
 */
function RecoveryBanner({ globals }: { globals: GlobalStatus }) {
  const spell = globals.stale_spell_seconds ?? 0;
  const escalations = globals.escalations ?? 0;
  if (globals.recovery_abandoned) {
    return (
      <div className="border-b border-border bg-surface-2 px-3 py-2 text-xs text-danger" role="status">
        Recovery abandoned after {formatIndianNumber(escalations, 0)} restart
        {escalations === 1 ? "" : "s"}: capture is running but receiving no data.
      </div>
    );
  }
  if (spell <= 0) return null;
  const armed = Boolean(globals.recovery_armed);
  return (
    <div
      className={`border-b border-border bg-surface-2 px-3 py-2 text-xs ${
        armed ? "text-danger" : "text-muted"
      }`}
      role="status"
    >
      {armed
        ? `Feed stale for ${formatIndianNumber(spell, 0)}s — capture will restart itself if it persists.`
        : `No ticks for ${formatIndianNumber(spell, 0)}s; the market is not trading yet.`}
    </div>
  );
}

/**
 * Total scheduled-vs-captured completeness, then why the rest is missing.
 *
 * The denominator is the session schedule rather than anything the process observed, which
 * is the only way downtime can appear at all: a process that was not running cannot count
 * the seconds it missed. The causes reconcile with the total, and whatever cannot be
 * attributed stays visible as "unknown" instead of being quietly dropped.
 */
function LossBreakdown({ globals }: { globals: GlobalStatus }) {
  const scheduled = globals.scheduled_seconds_elapsed ?? 0;
  if (scheduled <= 0) return null;
  const captured = globals.captured_seconds ?? 0;
  const missing = globals.missing_seconds ?? 0;
  const causes: Array<[string, number]> = [
    ["feed stale", globals.stale_feed_seconds ?? 0],
    ["downtime", globals.downtime_seconds ?? 0],
    ["write fail", globals.write_path_seconds ?? 0],
    ["unknown", globals.unclassified_seconds ?? 0],
  ];
  return (
    <div className="border-b border-border px-3 py-2 text-xs" aria-label="Loss breakdown">
      {/* One wrapping row rather than a totals line with the causes beneath it: the
          causes are the explanation OF the Missing figure, and at this panel's width they
          sit beside it instead of costing a second line. They still wrap as a group when
          the panel is narrow. */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-muted">
          Scheduled <span className="font-mono text-primary">{formatIndianNumber(scheduled, 0)}s</span>
        </span>
        <span className="text-muted">
          Captured <span className="font-mono text-primary">{formatIndianNumber(captured, 0)}s</span>
        </span>
        <span className={missing > 0 ? "text-danger" : "text-success"}>
          Missing <span className="font-mono">{formatIndianNumber(missing, 0)}s</span>
          {" "}({formatPercent(globals.scheduled_loss_pct ?? 0, 2)})
        </span>
        {missing > 0 &&
          causes
            .filter(([, seconds]) => seconds > 0)
            .map(([label, seconds]) => (
              <span key={label} className="text-muted">
                {label} <span className="font-mono text-secondary">{formatIndianNumber(seconds, 0)}s</span>
              </span>
            ))}
      </div>
    </div>
  );
}

/** Feed-health labels, deliberately distinct from the market phase (§23). */
const FEED_HEALTH_LABEL: Record<string, string> = {
  HEALTHY: "feed healthy",
  QUIET: "market quiet",
  ARTIFACT_STALE: "dataset stale",
  TRANSPORT_STALE: "feed dead",
  RECOVERY_PENDING: "restart pending",
  RECOVERY_ABANDONED: "recovery abandoned",
  INACTIVE: "not trading",
};

const FEED_HEALTH_SEVERITY: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  HEALTHY: "success",
  QUIET: "neutral",
  ARTIFACT_STALE: "warning",
  TRANSPORT_STALE: "danger",
  RECOVERY_PENDING: "danger",
  RECOVERY_ABANDONED: "danger",
  INACTIVE: "neutral",
};

/**
 * Market phase, pinned to the panel heading.
 *
 * It is the denominator for everything below it — a figure like "missing 600s" means
 * nothing until you know whether the exchange was open — so it belongs on the title line
 * rather than in a strip that can be scrolled past. It stays deliberately separate from
 * feed health (§23): PRE_OPEN + HEALTHY and OPEN + TRANSPORT_STALE are both meaningful,
 * and conflating them is what let a routine pre-open look like a dead feed.
 */
function MarketPhaseTag({ globals }: { globals: GlobalStatus }) {
  return (
    <span className="shrink-0 whitespace-nowrap text-xs text-muted" aria-label="Market phase">
      Phase <span className="font-mono text-secondary">{globals.market_phase ?? "--"}</span>
    </span>
  );
}

/**
 * Feed health, and any datasets that have stopped updating.
 *
 * Market phase used to share this strip; it now sits on the panel heading, leaving this
 * row to report only what the feed itself is doing.
 */
function FeedHealthStrip({ globals }: { globals: GlobalStatus }) {
  const health = globals.feed_health ?? null;
  if (!health) return null;
  const stale = globals.stale_artifacts ?? [];
  const severity = FEED_HEALTH_SEVERITY[health] ?? "neutral";
  const tone =
    severity === "danger"
      ? "text-danger"
      : severity === "warning"
        ? "text-warning"
        : severity === "success"
          ? "text-success"
          : "text-muted";
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-surface-2 px-3 py-1.5 text-xs"
      aria-label="Feed health"
    >
      <span className={tone}>
        {FEED_HEALTH_LABEL[health] ?? health}
      </span>
      {stale.length > 0 && (
        <span className="text-warning">not updating: {stale.join(", ")}</span>
      )}
    </div>
  );
}

export function DataLossDiagnostics({ globals }: { globals: GlobalStatus | null }) {
  const staleSeconds = globals?.stale_seconds ?? 0;
  const gridElapsed = globals?.grid_seconds_elapsed ?? 0;
  const stalePct = gridElapsed > 0 ? (staleSeconds / gridElapsed) * 100 : 0;
  const items = globals
    ? [
        {
          label: "Seconds lost",
          value: formatIndianNumber(globals.grid_seconds_lost ?? 0, 0),
          severity: globals.grid_seconds_lost ? "danger" as const : "success" as const,
        },
        {
          label: "Gap events",
          value: formatIndianNumber(globals.grid_gaps ?? 0, 0),
          severity: globals.grid_gaps ? "danger" as const : "success" as const,
        },
        {
          label: "Stale seconds",
          value: formatIndianNumber(staleSeconds, 0),
          detail: staleSeconds
            ? `${formatPercent(stalePct, 1)} of session not written`
            : "no stale data written",
          severity: staleSeconds ? "danger" as const : "success" as const,
        },
        {
          label: "Stale events",
          value: formatIndianNumber(globals.stale_events ?? 0, 0),
          severity: globals.stale_events ? "warning" as const : "success" as const,
        },
        {
          label: "Elapsed loss",
          value: formatPercent(globals.session_loss_pct ?? 0, 3),
          detail: "gaps only",
          severity: lossSeverity(globals.session_loss_pct),
        },
        {
          label: "Data loss",
          value: formatPercent(globals.data_loss_pct ?? 0, 3),
          detail: "incl. stale",
          severity: lossSeverity(globals.data_loss_pct),
        },
        {
          label: "Unmatched ticks",
          value: formatIndianNumber(globals.unmatched_ticks ?? 0, 0),
          severity: globals.unmatched_ticks ? "warning" as const : "neutral" as const,
        },
        {
          label: "Ticks / sec",
          value: formatIndianNumber(globals.ticks_per_sec ?? 0, 1),
          severity: "neutral" as const,
        },
        {
          label: "Reconnects",
          value: formatIndianNumber(globals.reconnects ?? 0, 0),
          severity: globals.exhausted
            ? "danger" as const
            : globals.reconnects
              ? "warning" as const
              : "neutral" as const,
        },
      ]
    : [];

  return (
    <Panel
      title="Data-loss diagnostics"
      subtitle="current session"
      className="h-full"
      action={globals ? <MarketPhaseTag globals={globals} /> : undefined}
    >
      {items.length > 0 && globals ? (
        <>
          <FeedHealthStrip globals={globals} />
          <RecoveryBanner globals={globals} />
          <LossBreakdown globals={globals} />
          <div className="grid grid-cols-3 gap-2 p-2">
            {items.map((item) => <Metric key={item.label} compact {...item} />)}
          </div>
        </>
      ) : (
        <div className="p-3">
          <StateMessage title="Awaiting data-loss telemetry">
            Gaps, stale intervals, and total market-data loss appear with capture status.
          </StateMessage>
        </div>
      )}
    </Panel>
  );
}
