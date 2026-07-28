"use client";

import React, { useRef, useSyncExternalStore } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { ResponsiveDisclosure } from "@/components/ui/ResponsiveDisclosure";
import {
  areOptionRowsEqual,
  GRID_KEYS,
  optionGridToRows,
  type OptionGrid,
  type OptionRowModel,
} from "@/lib/options/updates";
import { fmtCell, formatIndianNumber } from "@/lib/numberFormat";
import type { GridBlock } from "@/lib/wsTypes";

export type OptionChainData = OptionGrid;

type Column = { key: keyof GridBlock; label: string };

const CALL_COLUMNS: Column[] = [
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
const PUT_COLUMNS = [...CALL_COLUMNS].reverse();
const WHOLE = new Set<keyof GridBlock>(["oi", "change_in_oi", "volume"]);
const DESKTOP_QUERY = "(min-width: 1024px)";
const TABLE_COLUMN_COUNT = CALL_COLUMNS.length + PUT_COLUMNS.length + 1;

function subscribeToDesktopLayout(onChange: () => void): () => void {
  const query = window.matchMedia(DESKTOP_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isDesktopLayout(): boolean {
  return window.matchMedia(DESKTOP_QUERY).matches;
}

function useDesktopLayout(): boolean {
  return useSyncExternalStore(subscribeToDesktopLayout, isDesktopLayout, () => true);
}

function decimals(key: keyof GridBlock): number {
  if (WHOLE.has(key)) return 0;
  if (key === "gamma") return 6;
  if (["delta", "theta", "vega", "rho"].includes(key)) return 4;
  return 2;
}

function valueClass(key: keyof GridBlock, value: number): string {
  if (key !== "change" && key !== "change_in_oi") return "text-secondary";
  return value > 0 ? "text-success" : value < 0 ? "text-danger" : "text-muted";
}

function Markers({ row }: { row: OptionRowModel }) {
  return (
    <span className="inline-flex flex-wrap justify-center gap-1">
      {row.isSpotAtm && <span className="rounded border border-accent/50 px-1 text-[10px] text-accent">SPOT</span>}
      {row.isAtm && <span className="rounded border border-border-strong px-1 text-[10px] text-primary">ATM</span>}
      {row.isMaxPain && <span className="rounded border border-border-strong px-1 text-[10px] text-secondary">PAIN</span>}
    </span>
  );
}

function DetailGroup({
  title,
  keys,
  row,
}: {
  title: string;
  keys: (keyof GridBlock)[];
  row: OptionRowModel;
}) {
  return (
    <section>
      <h3 className="label mb-2 text-secondary">{title}</h3>
      <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 gap-y-1 text-xs">
        <span className="text-muted">Field</span><span className="text-accent">Call</span><span className="text-secondary">Put</span>
        {keys.map((key) => (
          <React.Fragment key={key}>
            <span className="text-muted">{CALL_COLUMNS.find((column) => column.key === key)?.label ?? key}</span>
            <span className={`text-right font-mono ${valueClass(key, row.call[key])}`}>{fmtCell(row.call[key], decimals(key))}</span>
            <span className={`text-right font-mono ${valueClass(key, row.put[key])}`}>{fmtCell(row.put[key], decimals(key))}</span>
          </React.Fragment>
        ))}
      </div>
    </section>
  );
}

const MobileOptionRow = React.memo(function MobileOptionRow({ row }: { row: OptionRowModel }) {
  return (
    <ResponsiveDisclosure
      id={`option-${row.strike}`}
      label={`Strike ${row.strike} details`}
      summary={
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-xs">
          <div>
            <div className="label text-accent">CALL</div>
            <div className="mt-1 font-mono text-primary">{fmtCell(row.call.ltp, 2)} LTP</div>
            <div className="font-mono text-muted">{fmtCell(row.call.oi, 0)} OI / {fmtCell(row.call.iv, 2)} IV</div>
          </div>
          <div className="text-center">
            <div className="font-mono text-sm font-semibold text-primary">{formatIndianNumber(row.strike, 0)}</div>
            <Markers row={row} />
          </div>
          <div className="text-right">
            <div className="label text-secondary">PUT</div>
            <div className="mt-1 font-mono text-primary">{fmtCell(row.put.ltp, 2)} LTP</div>
            <div className="font-mono text-muted">{fmtCell(row.put.oi, 0)} OI / {fmtCell(row.put.iv, 2)} IV</div>
          </div>
        </div>
      }
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <DetailGroup title="Price" keys={["bid", "ask", "ltp", "change"]} row={row} />
        <DetailGroup title="Flow" keys={["oi", "change_in_oi", "volume"]} row={row} />
        <DetailGroup title="Greeks" keys={["iv", "delta", "gamma", "theta", "vega", "rho"]} row={row} />
      </div>
    </ResponsiveDisclosure>
  );
}, (previous, next) => previous.row === next.row || areOptionRowsEqual(previous.row, next.row));

const DesktopOptionRow = React.memo(function DesktopOptionRow({ row, index }: { row: OptionRowModel; index: number }) {
  const rowTone = row.isAtm ? "bg-surface-3" : row.isMaxPain ? "bg-surface-2" : index % 2 ? "bg-surface-1" : "bg-canvas";
  return (
    <tr className={rowTone}>
      {CALL_COLUMNS.map((column) => (
        <td key={`call-${column.key}`} className={`whitespace-nowrap text-right font-mono ${row.isCallInTheMoney ? "bg-accent/[0.04]" : ""} ${valueClass(column.key, row.call[column.key])}`}>
          {fmtCell(row.call[column.key], decimals(column.key))}
        </td>
      ))}
      <td className="sticky left-0 z-20 whitespace-nowrap border-x border-border-strong bg-surface-3 text-center font-mono font-semibold text-primary">
        <div className="flex items-center justify-center gap-1">
          <span>{formatIndianNumber(row.strike, 0)}</span>
          <Markers row={row} />
        </div>
      </td>
      {PUT_COLUMNS.map((column) => (
        <td key={`put-${column.key}`} className={`whitespace-nowrap text-right font-mono ${row.isPutInTheMoney ? "bg-white/[0.025]" : ""} ${valueClass(column.key, row.put[column.key])}`}>
          {fmtCell(row.put[column.key], decimals(column.key))}
        </td>
      ))}
    </tr>
  );
}, (previous, next) => previous.index === next.index && (previous.row === next.row || areOptionRowsEqual(previous.row, next.row)));

function DesktopOptionTable({ rows }: { rows: OptionRowModel[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // TanStack Virtual manages mutable measurements internally; row inputs remain immutable.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 30,
    overscan: 10,
  });
  const virtualRows = rowVirtualizer.getVirtualItems();
  const beforeHeight = virtualRows[0]?.start ?? 0;
  const afterHeight = virtualRows.length > 0
    ? rowVirtualizer.getTotalSize() - (virtualRows.at(-1)?.end ?? 0)
    : 0;

  return (
    <div ref={scrollRef} className="max-h-[calc(100dvh-15rem)] overflow-auto rounded-[10px] border border-border">
      <table className="data-table min-w-max">
        <thead className="sticky top-0 z-30">
          <tr>
            <th colSpan={CALL_COLUMNS.length} className="border-r border-border-strong text-center text-accent">CALLS</th>
            <th rowSpan={2} className="sticky left-0 z-40 border-x border-border-strong text-center text-primary">
              <span className="block">STRIKE</span>
              <span className="mt-1 block text-[10px] font-normal text-muted">MARKERS</span>
            </th>
            <th colSpan={PUT_COLUMNS.length} className="border-l border-border-strong text-center text-secondary">PUTS</th>
          </tr>
          <tr>
            {CALL_COLUMNS.map((column) => <th key={`call-h-${column.key}`} className="text-right">{column.label}</th>)}
            {PUT_COLUMNS.map((column) => <th key={`put-h-${column.key}`} className="text-right">{column.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {beforeHeight > 0 && <tr aria-hidden="true"><td colSpan={TABLE_COLUMN_COUNT} style={{ height: beforeHeight, padding: 0 }} /></tr>}
          {virtualRows.map((virtualRow) => (
            <DesktopOptionRow
              key={rows[virtualRow.index].strike}
              row={rows[virtualRow.index]}
              index={virtualRow.index}
            />
          ))}
          {afterHeight > 0 && <tr aria-hidden="true"><td colSpan={TABLE_COLUMN_COUNT} style={{ height: afterHeight, padding: 0 }} /></tr>}
        </tbody>
      </table>
    </div>
  );
}

export default function OptionChainTable({
  data,
  rows = optionGridToRows(data),
}: {
  data: OptionChainData;
  rows?: OptionRowModel[];
}) {
  const isDesktop = useDesktopLayout();

  return isDesktop ? (
    <DesktopOptionTable rows={rows} />
  ) : (
      <div className="space-y-2">
        {rows.map((row) => <MobileOptionRow key={row.strike} row={row} />)}
      </div>
  );
}

export const OPTION_FIELD_COUNT = GRID_KEYS.length;
