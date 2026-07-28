"use client";

import React, { Fragment, useCallback, useMemo, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import StockDepthPanel from "@/components/StockDepthPanel";
import { PageFrame } from "@/components/ui/PageFrame";
import { PageHeader } from "@/components/ui/PageHeader";
import { StateMessage } from "@/components/ui/StateMessage";
import {
  areStockRowsEqual,
  depthFromBoard,
  legScalars,
  normalizeStockBoard,
  stockRows,
} from "@/lib/stockBoard";
import { fmtCell, formatClockTime, formatIndianNumber } from "@/lib/numberFormat";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { stocksConnection } from "@/lib/wsTopicConnection";
import { MSG, type StockBoardPayload, type StockDepthSnapshot, type StockLegName, type StockRow, type WsEnvelope } from "@/lib/wsTypes";

type ScalarGroups = Record<StockLegName, Record<string, number>>;

const LEG_LABELS: Record<StockLegName, string> = {
  spot: "Spot",
  fut_current: "Current future",
  fut_mid: "Mid future",
  fut_far: "Far future",
};

export default function StocksPage() {
  const [board, setBoard] = useState<StockBoardPayload | null>(null);
  const [projectedRows, setProjectedRows] = useState<StockRow[]>([]);
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const onEnvelope = useCallback((envelope: WsEnvelope) => {
    if (envelope.type !== MSG.STOCK_BOARD) return;
    const next = normalizeStockBoard(envelope.payload);
    if (!next) {
      setPayloadError("A stock-board update was malformed. Showing the last valid board.");
      return;
    }
    setPayloadError(null);
    setBoard(next);
    setProjectedRows((current) => stockRows(next, current));
  }, []);
  useTopicEnvelopes(stocksConnection, onEnvelope);

  const rows = useMemo(() => {
    const normalizedQuery = query.trim().toUpperCase();
    const filtered = normalizedQuery
      ? projectedRows.filter(
          (row) =>
            row.name.toUpperCase().includes(normalizedQuery) ||
            row.tradingsymbol.toUpperCase().includes(normalizedQuery),
        )
      : projectedRows;
    return [...filtered].sort((left, right) => left.name.localeCompare(right.name));
  }, [projectedRows, query]);

  const toggle = useCallback((symbol: string) => {
    setExpandedSymbol((current) => current === symbol ? null : symbol);
  }, []);

  return (
    <PageFrame>
      <PageHeader
        title="Stocks Board"
        description="Spot, three futures, live and daily spreads, scalars, and complete L1-L5 depth."
        actions={<ConnectionDot connection={stocksConnection} label="stocks" />}
      />
      <div className="panel page-toolbar">
        <label className="min-w-0 flex-1 sm:max-w-64">
          <span className="sr-only">Filter stocks by symbol</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter symbol"
            className="control min-h-11 w-full border-border bg-surface-2 px-3 text-sm text-primary placeholder:text-muted"
          />
        </label>
        <span className="ml-auto text-right text-xs text-muted">
          {board ? `${rows.length} / ${board.count} stocks` : "waiting for board"}
          {board ? ` / updated ${formatClockTime(board.timestamp)}` : ""}
        </span>
      </div>

      {payloadError && <StateMessage title="Malformed stock data rejected" severity="warning" role="alert">{payloadError}</StateMessage>}
      {rows.length === 0 ? (
        <StateMessage title={query ? "No symbols match the filter" : "Waiting for the stock board"}>
          {query ? "Clear or change the symbol filter." : "Start backend capture to publish the live F&O board."}
        </StateMessage>
      ) : (
        <>
          <div className="space-y-2 md:hidden">
            {rows.map((row) => {
              const isExpanded = expandedSymbol === row.tradingsymbol;
              return (
                <MobileStockRow
                  key={row.tradingsymbol}
                  row={row}
                  isExpanded={isExpanded}
                  onToggle={() => toggle(row.tradingsymbol)}
                  depth={isExpanded ? depthFromBoard(board, row.row) : null}
                  scalars={isExpanded ? scalarGroups(board, row.row) : null}
                />
              );
            })}
          </div>
          <div className="hidden max-h-[calc(100dvh-14rem)] overflow-auto rounded-[10px] border border-border md:block">
            <table className="data-table">
              <thead className="sticky top-0 z-30">
                <tr>
                  <th className="sticky left-0 z-40 text-left">Symbol</th>
                  <th className="text-right">Spot LTP</th>
                  <th className="text-right">Current future</th>
                  <th className="text-right">Mid future</th>
                  <th className="text-right">Far future</th>
                  <th className="text-right">Live spread</th>
                  <th className="text-right">Daily spread</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const isExpanded = expandedSymbol === row.tradingsymbol;
                  return (
                    <DesktopStockRow
                      key={row.tradingsymbol}
                      row={row}
                      isExpanded={isExpanded}
                      onToggle={() => toggle(row.tradingsymbol)}
                      depth={isExpanded ? depthFromBoard(board, row.row) : null}
                      scalars={isExpanded ? scalarGroups(board, row.row) : null}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </PageFrame>
  );
}

function scalarGroups(board: StockBoardPayload | null, row: number): ScalarGroups {
  return {
    spot: legScalars(board, "spot", row),
    fut_current: legScalars(board, "fut_current", row),
    fut_mid: legScalars(board, "fut_mid", row),
    fut_far: legScalars(board, "fut_far", row),
  };
}

function Spread({ value }: { value: number }) {
  return <span className={value > 0 ? "text-success" : value < 0 ? "text-danger" : "text-muted"}>{fmtCell(value, 2)}</span>;
}

function FutureSummary({ future }: { future?: { expiry: string; ltp: number; oi: number } }) {
  if (!future) return <span className="text-muted">--</span>;
  return (
    <span>
      <span className="font-mono text-primary">{fmtCell(future.ltp, 2)}</span>
      <span className="ml-1 text-xs text-muted">{future.expiry.slice(5)} / OI {formatIndianNumber(future.oi, 0)}</span>
    </span>
  );
}

function ExpandedStock({
  row,
  depth,
  scalars,
  id,
}: {
  row: StockRow;
  depth: StockDepthSnapshot | null;
  scalars: ScalarGroups | null;
  id: string;
}) {
  return (
    <div id={id} className="space-y-4 bg-canvas p-3">
      <section>
        <h3 className="label mb-2 text-secondary">Other futures</h3>
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <div><span className="text-muted">Mid: </span><FutureSummary future={row.futures[1]} /></div>
          <div><span className="text-muted">Far: </span><FutureSummary future={row.futures[2]} /></div>
        </div>
      </section>
      {scalars && (
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-4">
          {(Object.keys(LEG_LABELS) as StockLegName[]).map((leg) => (
            <ScalarGroup key={leg} label={LEG_LABELS[leg]} values={scalars[leg]} />
          ))}
        </div>
      )}
      <StockDepthPanel depth={depth} id={`${id}-depth`} />
    </div>
  );
}

function ScalarGroup({ label, values }: { label: string; values: Record<string, number> }) {
  return (
    <section className="rounded-lg border border-border bg-surface-1 p-2">
      <h3 className="mb-2 text-xs font-semibold text-primary">{label} scalars</h3>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        {Object.entries(values).map(([field, value]) => (
          <div key={field} className="contents">
            <dt className="text-muted">{field.replaceAll("_", " ")}</dt>
            <dd className="text-right font-mono text-secondary">{fmtCell(value, field.includes("quantity") || field === "volume" || field === "oi" ? 0 : 2)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

const MobileStockRow = React.memo(function MobileStockRow({
  row,
  isExpanded,
  onToggle,
  depth,
  scalars,
}: {
  row: StockRow;
  isExpanded: boolean;
  onToggle: () => void;
  depth: StockDepthSnapshot | null;
  scalars: ScalarGroups | null;
}) {
  const id = `mobile-stock-${row.tradingsymbol.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  return (
    <section className="disclosure">
      <button type="button" aria-expanded={isExpanded} aria-controls={id} onClick={onToggle} className="min-h-11 w-full px-3 py-2 text-left">
        <div className="grid grid-cols-[1fr_auto] gap-2 text-xs">
          <div>
            <div className="font-semibold text-primary">{row.tradingsymbol}</div>
            {row.name !== row.tradingsymbol && <div className="text-muted">{row.name}</div>}
            <div className="mt-1 font-mono text-secondary">
              Spot {fmtCell(row.spot_ltp, 2)} / Current {fmtCell(row.futures[0]?.ltp ?? 0, 2)}
            </div>
          </div>
          <div className="text-right font-mono">
            <div>Live <Spread value={row.live_spread} /></div>
            <div>Daily <Spread value={row.daily_spread} /></div>
          </div>
        </div>
      </button>
      {isExpanded && <ExpandedStock row={row} depth={depth} scalars={scalars} id={id} />}
    </section>
  );
}, stockRowPropsEqual);

const DesktopStockRow = React.memo(function DesktopStockRow({
  row,
  isExpanded,
  onToggle,
  depth,
  scalars,
}: {
  row: StockRow;
  isExpanded: boolean;
  onToggle: () => void;
  depth: StockDepthSnapshot | null;
  scalars: ScalarGroups | null;
}) {
  const id = `desktop-stock-${row.tradingsymbol.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  return (
    <Fragment>
      <tr>
        <td className="sticky left-0 z-20 bg-surface-1">
          <button type="button" aria-expanded={isExpanded} aria-controls={id} onClick={onToggle} className="control min-h-10 px-2 font-semibold text-primary hover:text-accent">
            {isExpanded ? "−" : "+"} {row.tradingsymbol}
            {row.name !== row.tradingsymbol && <span className="ml-1 font-normal text-muted">{row.name}</span>}
          </button>
        </td>
        <td className="text-right font-mono text-primary">{fmtCell(row.spot_ltp, 2)}</td>
        {row.futures.slice(0, 3).map((future) => <td key={future.expiry} className="text-right"><FutureSummary future={future} /></td>)}
        {Array.from({ length: Math.max(0, 3 - row.futures.length) }, (_, index) => <td key={`empty-${index}`} className="text-right text-muted">--</td>)}
        <td className="text-right font-mono"><Spread value={row.live_spread} /></td>
        <td className="text-right font-mono"><Spread value={row.daily_spread} /></td>
      </tr>
      {isExpanded && <tr><td colSpan={7} className="p-0"><ExpandedStock row={row} depth={depth} scalars={scalars} id={id} /></td></tr>}
    </Fragment>
  );
}, stockRowPropsEqual);

function stockRowPropsEqual(
  previous: { row: StockRow; isExpanded: boolean },
  next: { row: StockRow; isExpanded: boolean },
): boolean {
  if (previous.isExpanded !== next.isExpanded || next.isExpanded) return false;
  return areStockRowsEqual(previous.row, next.row);
}
