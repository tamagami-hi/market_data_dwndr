"use client";

import { useCallback, useMemo, useState } from "react";

import ConnectionDot from "@/components/ConnectionDot";
import OptionChainTable, { type OptionChainData } from "@/components/OptionChainTable";
import { MarketPageHeader } from "@/components/ui/MarketPageHeader";
import { PageFrame } from "@/components/ui/PageFrame";
import { StateMessage } from "@/components/ui/StateMessage";
import { formatIndianNumber } from "@/lib/numberFormat";
import {
  applyOptionDelta,
  normalizeMarketHeader,
  normalizeOptionDeltaPayload,
  normalizeOptionGridPayload,
  optionGridToRows,
  type OptionRowModel,
} from "@/lib/options/updates";
import { useTopicEnvelopes } from "@/lib/useTopic";
import { marketDataConnection } from "@/lib/wsTopicConnection";
import {
  MSG,
  type MarketHeaderPayload,
  type OptionGridPayload,
  type WsEnvelope,
} from "@/lib/wsTypes";

interface UnderlyingState {
  header?: MarketHeaderPayload;
  data?: OptionChainData;
  rows?: OptionRowModel[];
}

const PREFERRED_ORDER = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"];

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

export default function OptionChainPage() {
  const [bySymbol, setBySymbol] = useState<Record<string, UnderlyingState>>({});
  const [selected, setSelected] = useState<string | null>(null);

  const onEnvelope = useCallback((env: WsEnvelope) => {
    if (env.type === MSG.MARKET_HEADER) {
      const h = normalizeMarketHeader(env.payload);
      if (!h) return;
      setBySymbol((prev) => ({ ...prev, [h.underlying]: { ...prev[h.underlying], header: h } }));
      setSelected((cur) => cur ?? h.underlying);
    } else if (env.type === MSG.OPTION_GRID) {
      const p = normalizeOptionGridPayload(env.payload);
      if (!p) return;
      setBySymbol((prev) => {
        const data = gridToData(p);
        return {
          ...prev,
          [p.underlying]: {
            ...prev[p.underlying],
            data,
            rows: optionGridToRows(data, prev[p.underlying]?.rows),
          },
        };
      });
      setSelected((cur) => cur ?? p.underlying);
    } else if (env.type === MSG.OPTION_GRID_DELTA) {
      const p = normalizeOptionDeltaPayload(env.payload);
      if (!p) return;
      setBySymbol((prev) => {
        const cur = prev[p.underlying]?.data;
        if (!cur) return prev;
        const data = applyOptionDelta(cur, {
          changedIndices: p.changed_indices,
          calls: p.calls,
          puts: p.puts,
        });
        return {
          ...prev,
          [p.underlying]: {
            ...prev[p.underlying],
            data,
            rows: optionGridToRows(data, prev[p.underlying]?.rows),
          },
        };
      });
    }
  }, []);

  useTopicEnvelopes(marketDataConnection, onEnvelope);

  const symbols = useMemo(() => {
    const present = Object.keys(bySymbol);
    const custom = present
      .filter((symbol) => !PREFERRED_ORDER.includes(symbol))
      .sort((left, right) => left.localeCompare(right));
    return [...PREFERRED_ORDER, ...custom];
  }, [bySymbol]);

  const current = selected ? bySymbol[selected] : undefined;

  return (
    <PageFrame>
      <MarketPageHeader
        title="Option Chain"
        description="Complete live calls, strikes, puts, price, flow, and reconstructed Greeks."
        ariaLabel="Option Chain controls"
      >
        <div className="market-index-tabs" aria-label="Option indices">
          {symbols.map((sym) => (
            <button
              key={sym}
              onClick={() => setSelected(sym)}
              aria-pressed={selected === sym}
              disabled={!bySymbol[sym]}
              className={`control min-h-11 shrink-0 whitespace-nowrap px-3 text-sm ${
                selected === sym
                  ? "border-border bg-surface-3 text-accent"
                  : !bySymbol[sym]
                    ? "cursor-default text-muted"
                  : "text-secondary hover:bg-surface-2 hover:text-primary"
              }`}
            >
              {sym}
            </button>
          ))}
        </div>
        <div className="market-connection">
          <ConnectionDot
            connection={marketDataConnection}
            label="market data"
            telemetryProfile="option"
            telemetryTitle="Option telemetry"
            popoverClassName="option-telemetry-popover"
          />
        </div>
      </MarketPageHeader>

      {current?.header && <HeaderRibbon header={current.header} data={current.data} />}

      {current?.data ? (
        <OptionChainTable data={current.data} rows={current.rows} />
      ) : (
        <StateMessage title="Waiting for option-chain data">
          Start backend capture to publish live chains on <code>/ws/market-data</code>.
        </StateMessage>
      )}
    </PageFrame>
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
    <dl
      className="market-stat-strip"
      role="region"
      aria-label="Selected option-chain market summary"
      tabIndex={0}
    >
      <Stat label="Expiry" value={header.expiry} />
      <Stat label="Spot" value={formatIndianNumber(header.spot, 2)} />
      <Stat label="ATM" value={formatIndianNumber(header.atm, 0)} />
      <Stat label="VIX" value={formatIndianNumber(header.vix, 2)} />
      <Stat label="Risk-Free" value={`${formatIndianNumber(header.risk_free_rate * 100, 2)}%`} />
      <Stat label="Max Pain" value={data ? formatIndianNumber(data.maxPain, 0) : "--"} />
      <Stat
        label="Seq"
        value={formatIndianNumber(header.sequence, 0)}
      />
    </dl>
  );
}

function Stat({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="market-stat">
      <dt className="label text-muted">{label}</dt>
      <dd className="font-mono font-semibold text-primary">{value}</dd>
    </div>
  );
}
