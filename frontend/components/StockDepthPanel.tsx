import { formatIndianNumber } from "@/lib/numberFormat";
import type { DepthLevel, StockDepthSnapshot } from "@/lib/wsTypes";

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
    <div id={id} role="region" aria-label={`${depth.name} L5 market depth`} className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-4">
      {legs.map((leg) => <DepthTable key={leg.label} label={leg.label} depth={leg.depth} />)}
    </div>
  );
}

function DepthTable({ label, depth }: { label: string; depth: DepthLevel[] }) {
  return (
    <section className="min-w-0 rounded-lg border border-border bg-surface-1 p-2">
      <h3 className="mb-2 text-xs font-semibold text-primary">{label} L1-L5</h3>
      <div className="grid grid-cols-[auto_1fr_1fr] gap-x-2 gap-y-1 text-xs">
        <span className="text-muted">L</span>
        <span className="text-right text-secondary">Bid price / qty / orders</span>
        <span className="text-right text-secondary">Ask price / qty / orders</span>
        {depth.slice(0, 5).map((level) => (
          <DepthRow key={level.level} level={level} />
        ))}
      </div>
    </section>
  );
}

function DepthRow({ level }: { level: DepthLevel }) {
  return (
    <>
      <span data-depth-level className="font-mono text-muted">{level.level}</span>
      <span className="text-right font-mono text-success">
        {formatIndianNumber(level.bid_price, 2)} / {formatIndianNumber(level.bid_qty, 0)} / {formatIndianNumber(level.bid_orders, 0)}
      </span>
      <span className="text-right font-mono text-danger">
        {formatIndianNumber(level.ask_price, 2)} / {formatIndianNumber(level.ask_qty, 0)} / {formatIndianNumber(level.ask_orders, 0)}
      </span>
    </>
  );
}
