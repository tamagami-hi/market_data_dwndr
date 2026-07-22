import { formatIndianNumber } from "@/lib/numberFormat";
import type { DepthLevel, StockDepthSnapshot } from "@/lib/wsTypes";

export default function StockDepthPanel({
  depth,
  id,
  isLoading,
  error,
}: {
  depth: StockDepthSnapshot | null;
  id: string;
  isLoading: boolean;
  error: string | null;
}) {
  if (isLoading) {
    return <DepthState id={id} message="Loading the latest L5 order book…" />;
  }
  if (error || !depth) {
    return <DepthState id={id} message={error ?? "L5 order book is unavailable."} isError />;
  }
  const legs = [
    { label: "Spot", depth: depth.spot_depth },
    ...depth.futures.map((future) => ({
      label: future.label,
      depth: future.depth,
    })),
  ];

  return (
    <div
      id={id}
      role="region"
      aria-label={`${depth.name} L5 market depth`}
      className="grid gap-3 border-y border-zinc-700/70 bg-zinc-950/70 p-3 lg:grid-cols-2 2xl:grid-cols-4"
    >
      <p className="text-[11px] text-zinc-500 lg:col-span-2 2xl:col-span-4">
        On-demand snapshot loaded when this row was expanded. Collapse and reopen to refresh.
      </p>
      {legs.map((leg) => (
        <DepthTable key={leg.label} label={leg.label} depth={leg.depth} />
      ))}
    </div>
  );
}

function DepthTable({ label, depth }: { label: string; depth: DepthLevel[] }) {
  return (
    <section className="min-w-[25rem] rounded-md border border-zinc-800 bg-zinc-900/70 p-2">
      <h3 className="mb-2 text-xs font-medium text-zinc-300">{label} order book</h3>
      <table className="w-full border-collapse text-[10px] font-mono">
        <caption className="sr-only">{label} order book</caption>
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-500">
            <DepthHeading>Level</DepthHeading>
            <DepthHeading>Bid orders</DepthHeading>
            <DepthHeading>Bid qty</DepthHeading>
            <DepthHeading>Bid</DepthHeading>
            <DepthHeading>Ask</DepthHeading>
            <DepthHeading>Ask qty</DepthHeading>
            <DepthHeading>Ask orders</DepthHeading>
          </tr>
        </thead>
        <tbody>
          {depth.slice(0, 5).map((level) => (
            <tr key={level.level} className="border-b border-zinc-800/50 last:border-0">
              <DepthCell value={level.level} />
              <DepthCell value={level.bid_orders} />
              <DepthCell value={level.bid_qty} />
              <DepthPrice value={level.bid_price} tone="text-emerald-400" />
              <DepthPrice value={level.ask_price} tone="text-rose-400" />
              <DepthCell value={level.ask_qty} />
              <DepthCell value={level.ask_orders} />
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function DepthState({ id, message, isError = false }: { id: string; message: string; isError?: boolean }) {
  return (
    <div id={id} role={isError ? "alert" : "status"} className={`border-y border-zinc-700/70 bg-zinc-950/70 p-4 text-sm ${isError ? "text-red-300" : "text-zinc-400"}`}>
      {message}
    </div>
  );
}

function DepthHeading({ children }: { children: React.ReactNode }) {
  return <th className="px-1 py-1 text-right font-normal first:text-center">{children}</th>;
}

function DepthCell({ value }: { value: number }) {
  return <td className="px-1 py-1 text-right text-zinc-400 first:text-center">{formatIndianNumber(value, 0)}</td>;
}

function DepthPrice({ value, tone }: { value: number; tone: string }) {
  const display = value === 0 ? "-" : formatIndianNumber(value, 2);
  return <td className={`px-1 py-1 text-right ${tone}`}>{display}</td>;
}
