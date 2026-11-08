import { memo } from "react";

import { Panel } from "@/components/ui/Panel";
import { ResponsiveDisclosure } from "@/components/ui/ResponsiveDisclosure";
import { StateMessage } from "@/components/ui/StateMessage";
import type { CaptureHistory, CaptureHistorySession } from "@/lib/api";
import {
  LOSS_HIGHLIGHT_MIN_PCT,
  formatLoss,
  lossColor,
  lossRowStyle,
  lossSeverity,
} from "@/lib/monitor/lossColor";
import { formatBytes, formatIndianNumber } from "@/lib/numberFormat";

const SUBTITLE = `live and archived captures · tinted by capture loss above ${LOSS_HIGHLIGHT_MIN_PCT}% · violet: strikes mislabelled`;

/** Right-edge violet bar, so it never mixes with the loss tint's left bar. */
const MISLABELLED_BAR = "inset -3px 0 0 var(--mislabelled)";

function hasMislabelled(session: CaptureHistorySession): boolean {
  return (session.mislabelled_frames ?? 0) > 0;
}

function rowStyle(session: CaptureHistorySession) {
  const loss = lossRowStyle(session.loss_pct);
  if (!hasMislabelled(session)) return loss;
  return {
    ...loss,
    boxShadow: [loss?.boxShadow, MISLABELLED_BAR].filter(Boolean).join(", "),
  };
}

function mislabelledText(session: CaptureHistorySession): string {
  const frames = formatIndianNumber(session.mislabelled_frames ?? 0, 0);
  const sets = (session.mislabelled_artifacts ?? []).join(", ");
  return `strikes mislabelled: ${frames} index frames read as missing${sets ? ` (${sets})` : ""}`;
}

function MislabelledNote({ session }: { session: CaptureHistorySession }) {
  if (!hasMislabelled(session)) return null;
  return (
    <div className="mt-1 text-[11px] leading-snug text-mislabelled">{mislabelledText(session)}</div>
  );
}

function rowTitle(session: CaptureHistorySession): string {
  const loss =
    session.loss_pct == null
      ? "no capture session recorded for this date"
      : `capture loss ${formatLoss(session.loss_pct)}${session.outcome ? ` (${session.outcome.replace("_", " ")})` : ""}`;
  return hasMislabelled(session) ? `${loss}; ${mislabelledText(session)}` : loss;
}

function LossValue({ lossPct }: { lossPct: number | null | undefined }) {
  const severity = lossSeverity(lossPct);
  return (
    <span
      className={`font-mono ${severity === null ? "text-muted" : "font-semibold"}`}
      style={severity === null ? undefined : { color: lossColor(severity) }}
    >
      {formatLoss(lossPct)}
    </span>
  );
}

function captureState(session: CaptureHistorySession): string {
  if (session.raw_files > 0 && session.archived_files > 0) return "Archiving";
  if (session.raw_files > 0) return session.is_current ? "Recording" : "Raw";
  return "Archived";
}

export const StorageHistory = memo(function StorageHistory({
  history,
}: {
  history: CaptureHistory | null;
}) {
  if (!history) {
    return (
      <Panel title="Download history" subtitle={SUBTITLE} className="panel-compact h-full">
        <div className="p-3">
          <StateMessage title="Capture history unavailable">
            The backend has not returned a configured storage history.
          </StateMessage>
        </div>
      </Panel>
    );
  }
  const archiveShare =
    history.totals.total_bytes > 0
      ? (history.totals.archived_bytes / history.totals.total_bytes) * 100
      : 0;
  return (
    <Panel title="Download history" subtitle={SUBTITLE} className="panel-compact h-full monitor-history-panel">
      <div className="grid grid-cols-2 gap-px border-b border-border bg-border sm:grid-cols-4">
        {[
          ["Sessions", formatIndianNumber(history.totals.sessions, 0)],
          ["Files", formatIndianNumber(history.totals.data_files, 0)],
          ["Stored", formatBytes(history.totals.total_bytes)],
          ["Archived", `${archiveShare.toFixed(1)}%`],
        ].map(([label, value]) => (
          <div key={label} className="bg-surface-2 px-3 py-1">
            <div className="label text-muted">{label}</div>
            <div className="mt-1 font-mono text-sm text-primary">{value}</div>
          </div>
        ))}
      </div>
      <div className="max-h-96 space-y-2 overflow-y-auto p-2 md:hidden">
        {history.sessions.map((session) => (
          <ResponsiveDisclosure
            key={session.trading_date}
            id={`storage-${session.trading_date}`}
            label={`${session.trading_date} storage details`}
            summary={
              <div
                className="flex items-center justify-between gap-2 rounded px-1 text-xs"
                style={rowStyle(session)}
              >
                <div>
                  <div className="font-semibold text-primary">{session.trading_date}</div>
                  <div className="mt-1 text-muted">{captureState(session)} / {session.data_files} files</div>
                  <MislabelledNote session={session} />
                </div>
                <div className="text-right">
                  <div className="font-mono text-secondary">{formatBytes(session.total_bytes)}</div>
                  <div className="mt-1">loss <LossValue lossPct={session.loss_pct} /></div>
                </div>
              </div>
            }
          >
            <dl className="grid grid-cols-2 gap-2 text-xs">
              <div><dt className="text-muted">Raw</dt><dd className="font-mono text-primary">{formatBytes(session.raw_bytes)}</dd></div>
              <div><dt className="text-muted">Archived</dt><dd className="font-mono text-primary">{formatBytes(session.archived_bytes)}</dd></div>
              <div><dt className="text-muted">Indices</dt><dd className="text-primary">{session.indices.join(", ") || "None"}</dd></div>
              <div><dt className="text-muted">Stock files</dt><dd className="font-mono text-primary">{session.stock_files}</dd></div>
            </dl>
          </ResponsiveDisclosure>
        ))}
      </div>
      <div className="monitor-storage-scroll hidden min-h-0 flex-1 overflow-auto md:block">
        <table className="data-table monitor-storage-table">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="text-left">Session</th>
              <th className="text-right">Loss</th>
              <th className="text-left">State</th>
              <th className="text-right">Stored</th>
              <th className="text-right">Raw / archive</th>
              <th className="text-right">Files</th>
              <th className="text-left">Captured sets</th>
            </tr>
          </thead>
          <tbody>
            {history.sessions.map((session) => (
              <tr
                key={session.trading_date}
                style={rowStyle(session)}
                title={rowTitle(session)}
              >
                <td className="font-semibold text-primary">{session.trading_date}</td>
                <td className="text-right"><LossValue lossPct={session.loss_pct} /></td>
                <td className="text-secondary">{captureState(session)}</td>
                <td className="text-right font-mono text-primary">{formatBytes(session.total_bytes)}</td>
                <td className="text-right font-mono text-muted">{formatBytes(session.raw_bytes)} / {formatBytes(session.archived_bytes)}</td>
                <td className="text-right font-mono text-secondary">{session.data_files}</td>
                <td className="text-secondary">
                  {session.indices.join(", ") || "No indices"}
                  {session.stock_files > 0 ? ` / stocks (${session.stock_files})` : ""}
                  <MislabelledNote session={session} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
});
