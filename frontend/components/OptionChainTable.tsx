"use client";

import React from "react";

import { fmtCell, formatIndianNumber } from "@/lib/numberFormat";
import type { GridBlock } from "@/lib/wsTypes";

export interface OptionChainData {
  strikes: number[];
  calls: GridBlock;
  puts: GridBlock;
  spot: number;
  marketAtm: number;
  maxPain: number;
  spotAtm: number;
}

type Col = { key: keyof GridBlock; label: string };

const CALL_COLS: Col[] = [
  { key: "oi", label: "OI" },
  { key: "change_in_oi", label: "Chg OI" },
  { key: "volume", label: "Vol" },
  { key: "iv", label: "IV" },
  { key: "delta", label: "Delta" },
  { key: "gamma", label: "Gamma" },
  { key: "theta", label: "Theta/day" },
  { key: "vega", label: "Vega/1%" },
  { key: "rho", label: "Rho/1%" },
  { key: "bid", label: "Bid" },
  { key: "ask", label: "Ask" },
  { key: "ltp", label: "LTP" },
  { key: "change", label: "Chg" },
];

const PUT_COLS: Col[] = [
  { key: "change", label: "Chg" },
  { key: "ltp", label: "LTP" },
  { key: "bid", label: "Bid" },
  { key: "ask", label: "Ask" },
  { key: "rho", label: "Rho/1%" },
  { key: "vega", label: "Vega/1%" },
  { key: "theta", label: "Theta/day" },
  { key: "gamma", label: "Gamma" },
  { key: "delta", label: "Delta" },
  { key: "iv", label: "IV" },
  { key: "volume", label: "Vol" },
  { key: "change_in_oi", label: "Chg OI" },
  { key: "oi", label: "OI" },
];

const WHOLE = new Set<keyof GridBlock>(["oi", "change_in_oi", "volume"]);

function decimalsFor(key: keyof GridBlock): number {
  if (WHOLE.has(key)) return 0;
  if (key === "gamma") return 6;
  if (key === "delta" || key === "theta" || key === "vega" || key === "rho") return 4;
  return 2;
}

function cell(block: GridBlock, key: keyof GridBlock, i: number): string {
  return fmtCell(block[key][i] ?? 0, decimalsFor(key));
}

function changeClass(key: keyof GridBlock, val: number): string {
  if (key !== "change" && key !== "change_in_oi") return "text-zinc-300";
  if (val > 0) return "text-green-400";
  if (val < 0) return "text-red-400";
  return "text-zinc-300";
}

function Marker({ isSpot, isAtm, isMaxPain }: { isSpot: boolean; isAtm: boolean; isMaxPain: boolean }) {
  return (
    <span className="inline-flex gap-0.5">
      {isSpot && <Badge tone="sky">S</Badge>}
      {isAtm && <Badge tone="yellow">A</Badge>}
      {isMaxPain && <Badge tone="red">MP</Badge>}
    </span>
  );
}

function Badge({ tone, children }: { tone: "sky" | "yellow" | "red"; children: React.ReactNode }) {
  const tones = {
    sky: "bg-sky-500/20 text-sky-300",
    yellow: "bg-yellow-500/20 text-yellow-300",
    red: "bg-red-500/20 text-red-300",
  } as const;
  return (
    <span className={`rounded px-1 text-[10px] font-semibold ${tones[tone]}`}>{children}</span>
  );
}

const OptionChainRow = React.memo(function OptionChainRow({
  data,
  i,
}: {
  data: OptionChainData;
  i: number;
}) {
  const strike = data.strikes[i];
  const isAtm = Math.abs(strike - data.marketAtm) < 1;
  const isMaxPain = Math.abs(strike - data.maxPain) < 1;
  const isSpotAtm = Math.abs(strike - data.spotAtm) < 1;
  const itmCall = strike < data.spot;
  const itmPut = strike > data.spot;

  const rowBg =
    isAtm && isMaxPain
      ? "bg-lime-900/20"
      : isAtm
        ? "bg-yellow-900/20"
        : isMaxPain
          ? "bg-red-900/20"
          : isSpotAtm
            ? "bg-sky-950/30"
            : i % 2 === 0
              ? "bg-zinc-900"
              : "bg-zinc-900/50";

  return (
    <tr className={`${rowBg} hover:bg-zinc-700/30`}>
      {CALL_COLS.map((col) => (
        <td
          key={`c-${col.key}`}
          className={`px-1 py-1 text-right font-mono whitespace-nowrap ${
            itmCall ? "bg-green-900/10" : ""
          } ${changeClass(col.key, data.calls[col.key][i] ?? 0)}`}
        >
          {cell(data.calls, col.key, i)}
        </td>
      ))}
      <td className="w-24 px-1.5 py-1 text-center font-mono font-semibold whitespace-nowrap text-zinc-100">
        {formatIndianNumber(strike, 0)}
      </td>
      <td className="w-14 px-0.5 py-1 text-center whitespace-nowrap">
        <Marker isSpot={isSpotAtm} isAtm={isAtm} isMaxPain={isMaxPain} />
      </td>
      {PUT_COLS.map((col) => (
        <td
          key={`p-${col.key}`}
          className={`px-1 py-1 text-right font-mono whitespace-nowrap ${
            itmPut ? "bg-red-900/10" : ""
          } ${changeClass(col.key, data.puts[col.key][i] ?? 0)}`}
        >
          {cell(data.puts, col.key, i)}
        </td>
      ))}
    </tr>
  );
});

export default function OptionChainTable({ data }: { data: OptionChainData }) {
  return (
    <div className="overflow-auto rounded-lg border border-zinc-800" style={{ maxHeight: "calc(100vh - 230px)" }}>
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 z-10 bg-zinc-900/95 backdrop-blur">
          <tr>
            <th colSpan={CALL_COLS.length} className="border-b border-zinc-700 px-1 py-2 text-center font-semibold text-green-400">
              CALLS
            </th>
            <th className="border-b border-zinc-700 px-1 py-2 text-center font-semibold text-zinc-300">STRIKE</th>
            <th className="border-b border-zinc-700 px-0.5 py-2">&nbsp;</th>
            <th colSpan={PUT_COLS.length} className="border-b border-zinc-700 px-1 py-2 text-center font-semibold text-red-400">
              PUTS
            </th>
          </tr>
          <tr>
            {CALL_COLS.map((col) => (
              <th key={`ch-${col.key}`} className="border-b border-zinc-700 px-1 py-1.5 text-right font-normal text-zinc-400 whitespace-nowrap">
                {col.label}
              </th>
            ))}
            <th className="border-b border-zinc-700 px-1 py-1.5 text-center font-medium text-zinc-300">Strike</th>
            <th className="border-b border-zinc-700 px-0.5 py-1.5">&nbsp;</th>
            {PUT_COLS.map((col) => (
              <th key={`ph-${col.key}`} className="border-b border-zinc-700 px-1 py-1.5 text-right font-normal text-zinc-400 whitespace-nowrap">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.strikes.map((strike, i) => (
            <OptionChainRow key={strike} data={data} i={i} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
