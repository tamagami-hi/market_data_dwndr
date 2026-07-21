"use client";

import { useCallback, useMemo, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import { fmtCell, formatClockTime, formatIndianNumber } from "@/lib/numberFormat";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { stocksConnection } from "@/lib/wsTopicConnection";
import { MSG, type StockBoardPayload, type StockRow, type WsEnvelope } from "@/lib/wsTypes";

export default function StocksPage() {
  const [board, setBoard] = useState<StockBoardPayload | null>(null);
  const [query, setQuery] = useState("");

  const onEnvelope = useCallback((env: WsEnvelope) => {
    if (env.type !== MSG.STOCK_BOARD) return;
    setBoard(env.payload as StockBoardPayload);
  }, []);

  useTopicEnvelopes(stocksConnection, onEnvelope);

  const rows = useMemo(() => {
    const all = board?.stocks ?? [];
    const q = query.trim().toUpperCase();
    const filtered = q ? all.filter((s) => s.name.toUpperCase().includes(q)) : all;
    return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
  }, [board, query]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-zinc-100">Stocks Board</h1>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter symbol…"
          className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600"
        />
        <span className="text-xs text-zinc-500">
          {board ? `${rows.length} / ${board.stocks.length} stocks` : ""}
        </span>
        <div className="ml-auto flex items-center gap-4">
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
        <div className="overflow-auto rounded-lg border border-zinc-800" style={{ maxHeight: "calc(100vh - 200px)" }}>
          <table className="w-full border-collapse text-xs">
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
                <StockRowView key={row.tradingsymbol} row={row} zebra={i % 2 === 0} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StockRowView({ row, zebra }: { row: StockRow; zebra: boolean }) {
  const fut = (i: number) => row.futures[i];
  return (
    <tr className={`${zebra ? "bg-zinc-900" : "bg-zinc-900/50"} hover:bg-zinc-700/30`}>
      <td className="px-2 py-1.5 text-left font-medium text-zinc-100 whitespace-nowrap">
        {row.name}
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
  );
}

function FutureCell({ f }: { f?: { expiry: string; ltp: number; oi: number } }) {
  if (!f) {
    return <td className="px-2 py-1.5 text-right font-mono text-zinc-600">-</td>;
  }
  return (
    <td className="px-2 py-1.5 text-right font-mono text-zinc-300 whitespace-nowrap">
      <div>{fmtCell(f.ltp, 2)}</div>
      <div className="text-[10px] text-zinc-500">
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
