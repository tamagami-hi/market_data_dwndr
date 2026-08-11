"use client";

import React, { Fragment, useCallback, useMemo, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import StockDepthPanel from "@/components/StockDepthPanel";
import { MarketPageHeader } from "@/components/ui/MarketPageHeader";
import { PageFrame } from "@/components/ui/PageFrame";
import { StateMessage } from "@/components/ui/StateMessage";
import { indexFnoAsStockBoard, normalizeIndexFnoBoard } from "@/lib/indexFnoBoard";
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
const STOCK_TABLE_MIN_WIDTH = 1_112;

/**
 * Expansion is tracked as a namespaced key rather than a bare symbol.
 *
 * Index rows and stock rows are separate domains that can legitimately carry the same
 * name, and both feed the same single-open-row state and the same DOM `id`/`aria-controls`
 * pair — so an un-namespaced key would open two rows at once and emit duplicate ids.
 */
type RowGroup = "stock" | "index";

function rowKey(group: RowGroup, symbol: string): string {
  return `${group}:${symbol}`;
}

export default function StocksPage() {
  const [board, setBoard] = useState<StockBoardPayload | null>(null);
  const [projectedRows, setProjectedRows] = useState<StockRow[]>([]);
  // The index-F&O board arrives on the same `stocks` topic as a distinct message type and
  // is relabelled into the stock shape, so both groups share every projection and renderer.
  const [indexBoard, setIndexBoard] = useState<StockBoardPayload | null>(null);
  const [indexProjectedRows, setIndexProjectedRows] = useState<StockRow[]>([]);
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const onEnvelope = useCallback((envelope: WsEnvelope) => {
    if (envelope.type === MSG.INDEX_FNO_BOARD) {
      const nextIndex = normalizeIndexFnoBoard(envelope.payload);
      if (!nextIndex) {
        setPayloadError("An index-F&O update was malformed. Showing the last valid board.");
        return;
      }
      const adapted = indexFnoAsStockBoard(nextIndex);
      setPayloadError(null);
      setIndexBoard(adapted);
      setIndexProjectedRows((current) => stockRows(adapted, current));
      return;
    }
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

  const matchesQuery = useCallback(
    (row: StockRow, normalizedQuery: string) =>
      !normalizedQuery ||
      row.name.toUpperCase().includes(normalizedQuery) ||
      row.tradingsymbol.toUpperCase().includes(normalizedQuery),
    [],
  );

  const rows = useMemo(() => {
    const normalizedQuery = query.trim().toUpperCase();
    const filtered = projectedRows.filter((row) => matchesQuery(row, normalizedQuery));
    return [...filtered].sort((left, right) => left.name.localeCompare(right.name));
  }, [matchesQuery, projectedRows, query]);

  // Board order, NOT alphabetical: the index order is the configured capture order
  // (NIFTY, BANKNIFTY, …), which is how the rest of the system reports these rows.
  const indexRows = useMemo(() => {
    const normalizedQuery = query.trim().toUpperCase();
    return indexProjectedRows.filter((row) => matchesQuery(row, normalizedQuery));
  }, [indexProjectedRows, matchesQuery, query]);

  const toggle = useCallback((key: string) => {
    setExpandedKey((current) => current === key ? null : key);
  }, []);

  const stockStatus = board
    ? `${rows.length} / ${board.count} stocks`
    : null;
  const indexStatus = indexBoard ? `${indexRows.length} / ${indexBoard.count} indices` : null;
  const updatedAt = board ?? indexBoard;
  const boardStatus = updatedAt
    ? `${[stockStatus, indexStatus].filter(Boolean).join(" · ")} / updated ${formatClockTime(updatedAt.timestamp)}`
    : "waiting for board";
  const totalRows = rows.length + indexRows.length;

  return (
    <PageFrame className="stocks-page-frame">
      <MarketPageHeader
        title="Stocks Board"
        description="Spot, three futures, live and daily spreads, scalars, and complete L1-L5 depth."
        ariaLabel="Stocks Board controls"
      >
        <label className="market-stock-filter">
          <span className="sr-only">Filter stocks by symbol</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter symbol"
            className="control min-h-11 w-full border-border bg-surface-2 px-3 text-sm text-primary placeholder:text-muted"
          />
        </label>
        <span className="market-page-meta">
          {boardStatus}
        </span>
        <div className="market-connection">
          <ConnectionDot
            connection={stocksConnection}
            label="stocks"
            telemetryProfile="stocks"
            telemetryTitle="Stock telemetry"
            popoverClassName="stock-telemetry-popover"
          />
        </div>
      </MarketPageHeader>
      <div className="market-mobile-meta" aria-label="Stock board status">
        {boardStatus}
      </div>

      {payloadError && <StateMessage title="Malformed stock data rejected" severity="warning" role="alert">{payloadError}</StateMessage>}
      {totalRows === 0 ? (
        <StateMessage title={query ? "No symbols match the filter" : "Waiting for the stock board"}>
          {query ? "Clear or change the symbol filter." : "Start backend capture to publish the live F&O board."}
        </StateMessage>
      ) : (
        <>
          <div className="space-y-2 md:hidden">
            {indexRows.length > 0 && (
              <h2 className="label px-1 pt-1 text-secondary">Indices</h2>
            )}
            {indexRows.map((row) => {
              const key = rowKey("index", row.tradingsymbol);
              const isExpanded = expandedKey === key;
              return (
                <MobileStockRow
                  key={key}
                  row={row}
                  group="index"
                  isExpanded={isExpanded}
                  onToggle={() => toggle(key)}
                  depth={isExpanded ? depthFromBoard(indexBoard, row.row) : null}
                  scalars={isExpanded ? scalarGroups(indexBoard, row.row) : null}
                />
              );
            })}
            {indexRows.length > 0 && rows.length > 0 && (
              <h2 className="label px-1 pt-2 text-secondary">Stocks</h2>
            )}
            {rows.map((row) => {
              const key = rowKey("stock", row.tradingsymbol);
              const isExpanded = expandedKey === key;
              return (
                <MobileStockRow
                  key={key}
                  row={row}
                  group="stock"
                  isExpanded={isExpanded}
                  onToggle={() => toggle(key)}
                  depth={isExpanded ? depthFromBoard(board, row.row) : null}
                  scalars={isExpanded ? scalarGroups(board, row.row) : null}
                />
              );
            })}
          </div>
          <div
            data-stock-table-frame
            className="stock-table-frame hidden min-h-0 flex-1 overflow-x-auto overflow-y-auto rounded-[10px] border border-border md:block"
          >
            <table
              className="data-table stock-board-table table-fixed"
              style={{ minWidth: STOCK_TABLE_MIN_WIDTH }}
            >
              <caption className="sr-only">Live index, stock and futures summary</caption>
              <colgroup>
                <col style={{ width: 190 }} />
                <col style={{ width: 112 }} />
                <col style={{ width: 190 }} />
                <col style={{ width: 190 }} />
                <col style={{ width: 190 }} />
                <col style={{ width: 120 }} />
                <col style={{ width: 120 }} />
              </colgroup>
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
                {indexRows.length > 0 && <GroupHeaderRow label="Indices" count={indexRows.length} />}
                {indexRows.map((row) => {
                  const key = rowKey("index", row.tradingsymbol);
                  const isExpanded = expandedKey === key;
                  return (
                    <DesktopStockRow
                      key={key}
                      row={row}
                      group="index"
                      isExpanded={isExpanded}
                      onToggle={() => toggle(key)}
                      depth={isExpanded ? depthFromBoard(indexBoard, row.row) : null}
                      scalars={isExpanded ? scalarGroups(indexBoard, row.row) : null}
                    />
                  );
                })}
                {indexRows.length > 0 && rows.length > 0 && (
                  <GroupHeaderRow label="Stocks" count={rows.length} />
                )}
                {rows.map((row) => {
                  const key = rowKey("stock", row.tradingsymbol);
                  const isExpanded = expandedKey === key;
                  return (
                    <DesktopStockRow
                      key={key}
                      row={row}
                      group="stock"
                      isExpanded={isExpanded}
                      onToggle={() => toggle(key)}
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

/**
 * Section divider inside the single table body.
 *
 * One table rather than two keeps the seven columns aligned across both groups, which is
 * the whole point of showing indices in the stock board's format. It is not sticky — only
 * the column header is — so it scrolls away with its group.
 */
function GroupHeaderRow({ label, count }: { label: string; count: number }) {
  return (
    <tr className="stock-group-row">
      <th scope="colgroup" colSpan={7} className="bg-surface-2 text-left">
        <span className="label text-secondary">{label}</span>{" "}
        <span className="ml-2 font-mono text-xs font-normal text-muted">{count}</span>
      </th>
    </tr>
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
    <span className="stock-future-summary">
      <span className="block font-mono text-primary">{fmtCell(future.ltp, 2)}</span>
      <span className="mt-0.5 flex items-center justify-end gap-1.5 whitespace-nowrap text-[0.6875rem] text-muted">
        <span>{future.expiry.slice(5)}</span>
        <span aria-hidden="true">·</span>
        <span>OI {formatIndianNumber(future.oi, 0)}</span>
      </span>
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
      <StockDepthPanel depth={depth} id={`${id}-depth`} />
      {scalars && (
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-4">
          {(Object.keys(LEG_LABELS) as StockLegName[]).map((leg) => (
            <ScalarGroup key={leg} label={LEG_LABELS[leg]} values={scalars[leg]} />
          ))}
        </div>
      )}
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
  group,
  isExpanded,
  onToggle,
  depth,
  scalars,
}: {
  row: StockRow;
  group: RowGroup;
  isExpanded: boolean;
  onToggle: () => void;
  depth: StockDepthSnapshot | null;
  scalars: ScalarGroups | null;
}) {
  const id = `mobile-${group}-${row.tradingsymbol.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
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
  group,
  isExpanded,
  onToggle,
  depth,
  scalars,
}: {
  row: StockRow;
  group: RowGroup;
  isExpanded: boolean;
  onToggle: () => void;
  depth: StockDepthSnapshot | null;
  scalars: ScalarGroups | null;
}) {
  const id = `desktop-${group}-${row.tradingsymbol.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  return (
    <Fragment>
      <tr>
        <td className="sticky left-0 z-20 bg-surface-1">
          <button type="button" aria-expanded={isExpanded} aria-controls={id} onClick={onToggle} className="control inline-flex min-h-10 items-center gap-1.5 px-2 font-semibold text-primary hover:text-accent">
            <span className="w-2 text-muted" aria-hidden="true">{isExpanded ? "▾" : "▸"}</span>
            {row.tradingsymbol}
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
