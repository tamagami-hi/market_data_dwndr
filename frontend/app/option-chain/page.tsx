"use client";

import { useCallback, useMemo, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import OptionChainTable, { type OptionChainData } from "@/components/OptionChainTable";
import { formatIndianNumber } from "@/lib/numberFormat";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { marketDataConnection } from "@/lib/wsTopicConnection";
import {
  MSG,
  type GridBlock,
  type MarketHeaderPayload,
  type OptionGridPayload,
  type WsEnvelope,
} from "@/lib/wsTypes";

interface UnderlyingState {
  header?: MarketHeaderPayload;
  data?: OptionChainData;
}

const PREFERRED_ORDER = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"];

function gridToData(p: OptionGridPayload): OptionChainData {
  return {
    strikes: p.strikes,
    calls: p.calls,
    puts: p.puts,
    spot: p.spot,
    marketAtm: p.market_atm,
    maxPain: p.max_pain,
    spotAtm: p.spot_atm,
  };
}

function patchBlock(base: GridBlock, patch: Partial<GridBlock>, indices: number[]): GridBlock {
  const next: GridBlock = { ...base };
  (Object.keys(patch) as (keyof GridBlock)[]).forEach((key) => {
    const col = [...base[key]];
    const patchCol = patch[key] as number[] | undefined;
    if (patchCol) {
      indices.forEach((strikeIdx, j) => {
        col[strikeIdx] = patchCol[j];
      });
    }
    next[key] = col;
  });
  return next;
}

export default function OptionChainPage() {
  const [bySymbol, setBySymbol] = useState<Record<string, UnderlyingState>>({});
  const [selected, setSelected] = useState<string | null>(null);

  const onEnvelope = useCallback((env: WsEnvelope) => {
    if (env.type === MSG.MARKET_HEADER) {
      const h = env.payload as MarketHeaderPayload;
      setBySymbol((prev) => ({ ...prev, [h.underlying]: { ...prev[h.underlying], header: h } }));
      setSelected((cur) => cur ?? h.underlying);
    } else if (env.type === MSG.OPTION_GRID) {
      const p = env.payload as OptionGridPayload;
      setBySymbol((prev) => ({ ...prev, [p.underlying]: { ...prev[p.underlying], data: gridToData(p) } }));
      setSelected((cur) => cur ?? p.underlying);
    } else if (env.type === MSG.OPTION_GRID_DELTA) {
      const p = env.payload as {
        underlying: string;
        changed_indices: number[];
        calls: Partial<GridBlock>;
        puts: Partial<GridBlock>;
      };
      setBySymbol((prev) => {
        const cur = prev[p.underlying]?.data;
        if (!cur) return prev;
        return {
          ...prev,
          [p.underlying]: {
            ...prev[p.underlying],
            data: {
              ...cur,
              calls: patchBlock(cur.calls, p.calls, p.changed_indices),
              puts: patchBlock(cur.puts, p.puts, p.changed_indices),
            },
          },
        };
      });
    }
  }, []);

  useTopicEnvelopes(marketDataConnection, onEnvelope);

  const symbols = useMemo(() => {
    const present = Object.keys(bySymbol);
    return present.sort((a, b) => {
      const ia = PREFERRED_ORDER.indexOf(a);
      const ib = PREFERRED_ORDER.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
  }, [bySymbol]);

  const current = selected ? bySymbol[selected] : undefined;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-zinc-100">Option Chain</h1>
        <div className="flex gap-1">
          {symbols.map((sym) => (
            <button
              key={sym}
              onClick={() => setSelected(sym)}
              className={`rounded px-3 py-1 text-sm ${
                selected === sym
                  ? "bg-sky-500/15 text-sky-300"
                  : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
              }`}
            >
              {sym}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <ConnectionDot connection={marketDataConnection} label="market-data" />
        </div>
      </header>

      {current?.header && <HeaderRibbon header={current.header} data={current.data} />}

      {current?.data ? (
        <OptionChainTable data={current.data} />
      ) : (
        <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-900/30 p-10 text-center text-sm text-zinc-500">
          Waiting for option-chain data on <code className="text-zinc-400">/ws/market-data</code>…
          start the backend capture to stream live chains.
        </div>
      )}
    </div>
  );
}

function HeaderRibbon({
  header,
  data,
}: {
  header: MarketHeaderPayload;
  data?: OptionChainData;
}) {
  return (
    <div className="flex flex-wrap gap-6 rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-2.5 text-sm">
      <Stat label="Expiry" value={header.expiry} />
      <Stat label="Spot" value={formatIndianNumber(header.spot, 2)} />
      <Stat label="ATM" value={formatIndianNumber(header.atm, 0)} />
      <Stat label="VIX" value={formatIndianNumber(header.vix, 2)} />
      <Stat label="Risk-Free" value={`${formatIndianNumber(header.risk_free_rate * 100, 2)}%`} />
      {data && <Stat label="Max Pain" value={formatIndianNumber(data.maxPain, 0)} />}
      <Stat label="Seq" value={formatIndianNumber(header.sequence, 0)} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className="font-semibold text-zinc-100">{value}</span>
    </div>
  );
}
