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

type Column = { key: keyof GridBlock; label: string; shortLabel: string; weight: number };

const CALL_COLUMNS: Column[] = [
  { key: "oi", label: "OI", shortLabel: "OI", weight: 38 },
  { key: "change_in_oi", label: "Chg OI", shortLabel: "ΔOI", weight: 40 },
  { key: "volume", label: "Vol", shortLabel: "Vol", weight: 38 },
  { key: "iv", label: "IV", shortLabel: "IV", weight: 42 },
  { key: "delta", label: "Delta", shortLabel: "Δ", weight: 51 },
  { key: "gamma", label: "Gamma", shortLabel: "Γ", weight: 68 },
  { key: "theta", label: "Theta/day", shortLabel: "Θ/d", weight: 55 },
  { key: "vega", label: "Vega/1%", shortLabel: "V/1%", weight: 51 },
  { key: "rho", label: "Rho/1%", shortLabel: "ρ/1%", weight: 51 },
  { key: "bid", label: "Bid", shortLabel: "Bid", weight: 52 },
  { key: "ask", label: "Ask", shortLabel: "Ask", weight: 52 },
  { key: "ltp", label: "LTP", shortLabel: "LTP", weight: 52 },
  { key: "change", label: "Chg", shortLabel: "Chg", weight: 52 },
];
const PUT_COLUMNS = [...CALL_COLUMNS].reverse();
const WHOLE = new Set<keyof GridBlock>(["oi", "change_in_oi", "volume"]);
const DESKTOP_QUERY = "(min-width: 1280px)";
const STRIKE_COLUMN_WEIGHT = 92;
const TABLE_COLUMN_COUNT = CALL_COLUMNS.length + PUT_COLUMNS.length + 1;
const TABLE_WEIGHT = CALL_COLUMNS.reduce((sum, column) => sum + column.weight, 0) * 2
  + STRIKE_COLUMN_WEIGHT;

function columnWidth(weight: number): string {
  return `${(weight / TABLE_WEIGHT) * 100}%`;
}

interface MarkerFlags {
  isSpotAtm: boolean;
  isAtm: boolean;
  isMaxPain: boolean;
}

export type OptionMarkerVariant =
  | "spot"
  | "atm"
  | "pain"
  | "spot-atm"
  | "spot-pain"
  | "atm-pain"
  | "spot-atm-pain";

export function optionMarkerCode(flags: MarkerFlags): string | null {
  const count = Number(flags.isSpotAtm) + Number(flags.isAtm) + Number(flags.isMaxPain);
  if (count === 0) return null;
  if (count === 3) return "S-A-M";
  if (count === 2) {
    if (!flags.isMaxPain) return "SA";
    if (!flags.isAtm) return "SM";
    return "AM";
  }
  if (flags.isSpotAtm) return "SP";
  if (flags.isAtm) return "ATM";
  return "MP";
}

export function optionMarkerVariant(flags: MarkerFlags): OptionMarkerVariant | null {
  if (flags.isSpotAtm && flags.isAtm && flags.isMaxPain) return "spot-atm-pain";
  if (flags.isSpotAtm && flags.isAtm) return "spot-atm";
  if (flags.isSpotAtm && flags.isMaxPain) return "spot-pain";
  if (flags.isAtm && flags.isMaxPain) return "atm-pain";
  if (flags.isSpotAtm) return "spot";
  if (flags.isAtm) return "atm";
  if (flags.isMaxPain) return "pain";
  return null;
}

function markerDescription(flags: MarkerFlags): string {
  return [
    flags.isSpotAtm ? "spot" : null,
    flags.isAtm ? "ATM" : null,
    flags.isMaxPain ? "max pain" : null,
  ].filter(Boolean).join(", ");
}

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

function compactDesktopValue(key: keyof GridBlock, value: number): string {
  if (value === 0 || Number.isNaN(value)) return "-";
  if (WHOLE.has(key)) {
    return Math.abs(value) >= 100_000 ? fmtCell(value, 0) : Math.round(value).toString();
  }
  const requestedDecimals = decimals(key);
  const integerDigits = Math.abs(Math.trunc(value)).toString().length;
  const effectiveDecimals = integerDigits >= 4 && requestedDecimals > 0 ? 1 : requestedDecimals;
  return value.toFixed(effectiveDecimals);
}

function exactValue(key: keyof GridBlock, value: number): string {
  return formatIndianNumber(value, decimals(key));
}

function Markers({ row }: { row: OptionRowModel }) {
  const code = optionMarkerCode(row);
  const variant = optionMarkerVariant(row);
  if (!code || !variant) return null;
  return (
    <span
      className="option-marker"
      data-marker-variant={variant}
      aria-label={`${markerDescription(row)} marker`}
    >
      {code}
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
        <td
          key={`call-${column.key}`}
          aria-label={exactValue(column.key, row.call[column.key])}
          className={`whitespace-nowrap text-right font-mono ${row.isCallInTheMoney ? "bg-accent/[0.04]" : ""} ${valueClass(column.key, row.call[column.key])}`}
        >
          {compactDesktopValue(column.key, row.call[column.key])}
        </td>
      ))}
      <td className="sticky left-0 z-20 whitespace-nowrap border-x border-border-strong bg-surface-3 text-center font-mono font-semibold text-primary">
        <div className="flex items-center justify-center gap-1">
          <span>{formatIndianNumber(row.strike, 0)}</span>
          <Markers row={row} />
        </div>
      </td>
      {PUT_COLUMNS.map((column) => (
        <td
          key={`put-${column.key}`}
          aria-label={exactValue(column.key, row.put[column.key])}
          className={`whitespace-nowrap text-right font-mono ${row.isPutInTheMoney ? "bg-white/[0.025]" : ""} ${valueClass(column.key, row.put[column.key])}`}
        >
          {compactDesktopValue(column.key, row.put[column.key])}
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
    estimateSize: () => 25,
    overscan: 10,
  });
  const virtualRows = rowVirtualizer.getVirtualItems();
  const beforeHeight = virtualRows[0]?.start ?? 0;
  const afterHeight = virtualRows.length > 0
    ? rowVirtualizer.getTotalSize() - (virtualRows.at(-1)?.end ?? 0)
    : 0;

  return (
    <div
      ref={scrollRef}
      data-option-table-frame
      className="mx-auto max-h-[calc(100dvh-13rem)] w-full max-w-[1600px] overflow-x-hidden overflow-y-auto rounded-[10px] border border-border"
    >
      <table
        className="data-table option-chain-table table-fixed"
        style={{ width: "100%" }}
      >
        <colgroup>
          {CALL_COLUMNS.map((column) => (
            <col key={`call-col-${column.key}`} style={{ width: columnWidth(column.weight) }} />
          ))}
          <col style={{ width: columnWidth(STRIKE_COLUMN_WEIGHT) }} />
          {PUT_COLUMNS.map((column) => (
            <col key={`put-col-${column.key}`} style={{ width: columnWidth(column.weight) }} />
          ))}
        </colgroup>
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
            {CALL_COLUMNS.map((column) => (
              <th key={`call-h-${column.key}`} aria-label={column.label} className="text-right">
                {column.shortLabel}
              </th>
            ))}
            {PUT_COLUMNS.map((column) => (
              <th key={`put-h-${column.key}`} aria-label={column.label} className="text-right">
                {column.shortLabel}
              </th>
            ))}
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
