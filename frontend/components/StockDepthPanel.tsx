import { fmtCell, formatIndianNumber } from "@/lib/numberFormat";
import type { DepthLevel, StockDepthSnapshot } from "@/lib/wsTypes";

type DepthValue = { display: string; exact: string };

const DEPTH_COUNT_UNITS = [
  { threshold: 10_000_000, divisor: 10_000_000, suffix: "Cr" },
  { threshold: 100_000, divisor: 100_000, suffix: "L" },
  { threshold: 10_000, divisor: 1_000, suffix: "K" },
] as const;

export function formatDepthCount(value: number): DepthValue {
  const exact = formatIndianNumber(value, 0);
  const magnitude = Math.abs(value);
  const unit = DEPTH_COUNT_UNITS.find(({ threshold }) => magnitude >= threshold);
  if (!unit) return { display: exact, exact };
  return {
    display: `${formatIndianNumber(value / unit.divisor, 1)}${unit.suffix}`,
    exact,
  };
}

export default function StockDepthPanel({
  depth,
  id,
}: {
  depth: StockDepthSnapshot | null;
  id: string;
}) {
  if (!depth) {
    return <div id={id} role="status" className="p-3 text-sm text-muted">Waiting for the live order book.</div>;
  }
  const legs = [
    { label: "Spot", depth: depth.spot_depth },
    ...depth.futures.map((future) => ({ label: future.label, depth: future.depth })),
  ];
  return (
    <div id={id} role="region" aria-label={`${depth.name} L5 market depth`} className="stock-depth-grid">
      {legs.map((leg) => <DepthTable key={leg.label} label={leg.label} depth={leg.depth} />)}
    </div>
  );
}

function DepthTable({ label, depth }: { label: string; depth: DepthLevel[] }) {
  return (
    <section className="stock-depth-card">
      <h3 className="mb-2 text-xs font-semibold text-primary">{label} L1-L5</h3>
      <div className="min-w-0">
        <table className="stock-depth-table w-full table-fixed border-collapse font-mono">
          <caption className="sr-only">{label} L1-L5 order book</caption>
          <DepthColumns />
          <DepthHeader />
          <tbody>
            {depth.slice(0, 5).map((level) => (
              <DepthRow key={level.level} level={level} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DepthColumns() {
  return (
    <>
      <colgroup>
        <col className="w-[12%]" />
        <col className="w-[14%]" />
        <col className="w-[19%]" />
      </colgroup>
      <colgroup><col className="w-[10%]" /></colgroup>
      <colgroup>
        <col className="w-[19%]" />
        <col className="w-[14%]" />
        <col className="w-[12%]" />
      </colgroup>
    </>
  );
}

function DepthHeader() {
  return (
    <thead>
      <tr className="border-b border-border text-[0.625rem] uppercase tracking-wide">
        <th scope="colgroup" colSpan={3} className="px-1 py-1 text-center font-semibold text-success">Bid</th>
        <th scope="col" rowSpan={2} aria-label="Level" className="border-x border-border px-1 py-1 text-center font-semibold text-muted">L</th>
        <th scope="colgroup" colSpan={3} className="px-1 py-1 text-center font-semibold text-danger">Ask</th>
      </tr>
      <tr className="border-b border-border text-muted">
        <DepthHeading label="Bid orders">Ord</DepthHeading>
        <DepthHeading label="Bid qty">Qty</DepthHeading>
        <DepthHeading label="Bid price">Price</DepthHeading>
        <DepthHeading label="Ask price">Price</DepthHeading>
        <DepthHeading label="Ask qty">Qty</DepthHeading>
        <DepthHeading label="Ask orders">Ord</DepthHeading>
      </tr>
    </thead>
  );
}

function DepthRow({ level }: { level: DepthLevel }) {
  return (
    <tr className="border-b border-border/60 last:border-0">
      <DepthCell value={level.bid_orders} isCount />
      <DepthCell value={level.bid_qty} isCount />
      <DepthCell value={level.bid_price} decimals={2} tone="text-success" />
      <td data-depth-level className="border-x border-border px-1 py-1 text-center text-muted">
        {formatIndianNumber(level.level, 0)}
      </td>
      <DepthCell value={level.ask_price} decimals={2} tone="text-danger" />
      <DepthCell value={level.ask_qty} isCount />
      <DepthCell value={level.ask_orders} isCount />
    </tr>
  );
}

function DepthHeading({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <th scope="col" aria-label={label} className="px-1 py-1 text-right font-normal">
      {children}
    </th>
  );
}

function DepthCell({
  value,
  decimals = 0,
  tone = "text-secondary",
  isCount = false,
}: {
  value: number;
  decimals?: number;
  tone?: string;
  isCount?: boolean;
}) {
  const formatted = isCount
    ? formatDepthCount(value)
    : {
        display: value === 0 ? formatIndianNumber(value, decimals) : fmtCell(value, decimals),
        exact: formatIndianNumber(value, decimals),
      };
  return (
    <td
      aria-label={formatted.exact}
      className={`overflow-hidden px-0.5 py-1 text-right tabular-nums ${tone}`}
    >
      {formatted.display}
    </td>
  );
}
