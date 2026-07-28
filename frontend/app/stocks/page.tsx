"use client";

import { Fragment, useCallback, useMemo, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import StockDepthPanel from "@/components/StockDepthPanel";
import { depthFromBoard, stockRows } from "@/lib/stockBoard";
import { fmtCell, formatClockTime, formatIndianNumber } from "@/lib/numberFormat";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { stocksConnection } from "@/lib/wsTopicConnection";
import { MSG, type StockBoardPayload, type StockRow, type WsEnvelope } from "@/lib/wsTypes";

export default function StocksPage() {
  const [board, setBoard] = useState<StockBoardPayload | null>(null);
  const [query, setQuery] = useState("");
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);

  const onEnvelope = useCallback((env: WsEnvelope) => {
    if (env.type !== MSG.STOCK_BOARD) return;
    setBoard(env.payload as StockBoardPayload);
  }, []);

  useTopicEnvelopes(stocksConnection, onEnvelope);

  const rows = useMemo(() => {
    const all = stockRows(board);
    const q = query.trim().toUpperCase();
    const filtered = q ? all.filter((s) => s.name.toUpperCase().includes(q)) : all;
    return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
  }, [board, query]);

  // Expanding a row is now purely local: L1-L5 depth for every leg is already in the
  // live board, so there is no fetch, no loading state, and the book keeps updating
  // every second instead of freezing until the row is collapsed and reopened.
  const toggleDepth = useCallback((row: StockRow) => {
    setExpandedSymbol((cur) => (cur === row.tradingsymbol ? null : row.tradingsymbol));
  }, []);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-lg font-semibold text-zinc-100 sm:text-xl">Stocks Board</h1>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter symbol…"
          aria-label="Filter stocks by symbol"
          className="min-w-0 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600 sm:max-w-56 sm:flex-none"
        />
        <span className="text-xs text-zinc-500">
          {board ? `${rows.length} / ${board.count} stocks` : ""}
        </span>
        <div className="flex w-full items-center gap-3 sm:ml-auto sm:w-auto sm:gap-4">
          {board && (
            <span className="text-xs text-zinc-500">
              updated {formatClockTime(board.timestamp)}
            </span>
          )}
          <ConnectionDot connection={stocksConnection} label="stocks" />
        </div>
      </header>

      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-900/30 p-10 text-center text-sm text-zinc-500">
          Waiting for the F&amp;O board on <code className="text-zinc-400">/ws/stocks</code>…
          start the backend capture to stream the stock matrix.
        </div>
      ) : (
        <>
          <p className="mb-1 text-[0.6875rem] text-zinc-500 sm:hidden" aria-hidden="true">
            Swipe sideways for futures &amp; spreads →
          </p>
          <div className="max-h-[70svh] overflow-auto overscroll-x-contain rounded-lg border border-zinc-800 lg:max-h-[calc(100dvh-13rem)]">
            <table className="w-full border-collapse text-[0.6875rem] sm:text-xs">
            <thead className="sticky top-0 z-10 bg-zinc-900/95 backdrop-blur">
              <tr className="text-zinc-400">
                <Th className="text-left">Symbol</Th>
                <Th>Spot LTP</Th>
                <Th>Current Fut</Th>
                <Th>Mid Fut</Th>
                <Th>Far Fut</Th>
                <Th>Live Spread</Th>
                <Th>Daily Spread</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <StockRowView
                  key={row.tradingsymbol}
                  row={row}
                  zebra={i % 2 === 0}
                  isExpanded={expandedSymbol === row.tradingsymbol}
                  depth={
                    expandedSymbol === row.tradingsymbol
                      ? depthFromBoard(board, row.row)
                      : null
                  }
                  onToggle={() => toggleDepth(row)}
                />
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}

interface StockRowViewProps {
  row: StockRow;
  zebra: boolean;
  isExpanded: boolean;
  onToggle: () => void;
  depth: import("@/lib/wsTypes").StockDepthSnapshot | null;
}

function StockRowView({ row, zebra, isExpanded, onToggle, depth }: StockRowViewProps) {
  const fut = (i: number) => row.futures[i];
  const panelId = `depth-${row.tradingsymbol.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  return (
    <Fragment>
      <tr className={`${zebra ? "bg-zinc-900" : "bg-zinc-900/50"} hover:bg-zinc-700/30`}>
        <td className="px-2 py-1.5 text-left font-medium text-zinc-100 whitespace-nowrap">
          <button
            type="button"
            aria-expanded={isExpanded}
            aria-controls={panelId}
            aria-label={`${isExpanded ? "Hide" : "Show"} L5 depth for ${row.name}`}
            onClick={onToggle}
            className="inline-flex items-center gap-2 rounded-sm text-left hover:text-cyan-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400"
          >
            <span aria-hidden="true" className="w-2 text-zinc-500">
              {isExpanded ? "▾" : "▸"}
            </span>
            {row.name}
          </button>
        </td>
        <td className="px-2 py-1.5 text-right font-mono text-zinc-200">
          {fmtCell(row.spot_ltp, 2)}
        </td>
        <FutureCell f={fut(0)} />
        <FutureCell f={fut(1)} />
        <FutureCell f={fut(2)} />
        <SpreadCell value={row.live_spread} />
        <SpreadCell value={row.daily_spread} />
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={7} className="p-0">
            <StockDepthPanel depth={depth} id={panelId} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function FutureCell({ f }: { f?: { expiry: string; ltp: number; oi: number } }) {
  if (!f) {
    return <td className="px-2 py-1.5 text-right font-mono text-zinc-600">-</td>;
  }
  return (
    <td className="px-2 py-1.5 text-right font-mono text-zinc-300 whitespace-nowrap">
      <div>{fmtCell(f.ltp, 2)}</div>
      <div className="text-[0.625rem] text-zinc-500">
        {f.expiry.slice(5)} · OI {formatIndianNumber(f.oi, 0)}
      </div>
    </td>
  );
}

function SpreadCell({ value }: { value: number }) {
  const tone = value > 0 ? "text-green-400" : value < 0 ? "text-red-400" : "text-zinc-400";
  return <td className={`px-2 py-1.5 text-right font-mono ${tone}`}>{fmtCell(value, 2)}</td>;
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <th className={`border-b border-zinc-700 px-2 py-2 text-right font-normal ${className}`}>
      {children}
    </th>
  );
}
