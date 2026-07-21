---
title: Depth-Level Research (L1 vs L2…L5)
area: reference
type: research
status: locked
tags: [area/reference, type/research, status/locked]
up: "[[Reference-MOC]]"
related: ["[[bin-format]]", "[[stocks-capture]]", "[[decisions-and-open-questions]]"]
---

# Depth-Level Research Notes (L1 vs L2…L5)

Notes behind the decision **indices = L1, stocks = L5**. Sources linked; content
paraphrased for licensing compliance.

## Levels are depth, not separate feed products

An order book has price levels each side (price, quantity, order-count). "Level N"
counts down from the touch. Vendors group these into three tiers:

- **L1 / top-of-book (NBBO):** best bid/ask only; cheaper, for price/chart traders who
  don't need depth ([EdgeClear](https://support.edgeclear.com/portal/en/kb/articles/top-of-book-versus-depth-of-market-data)).
- **L2 / depth-of-book (aggregated by price):** several levels;
  [Databento](https://databento.com/tick-data) sells this as MBP-10 (top 10 levels);
  [Capital.com](https://capital.com/en-ae/learn/glossary/market-data-definition)
  describes L2 as multiple bid/ask levels for day traders.
- **L3 / order-by-order (MBO):** every individual order; institutional tier
  ([Capital.com](https://capital.com/en-ae/learn/glossary/market-data-definition)).

[LSEG Tick History](https://www.lseg.com/en/data-analytics/market-data/data-feeds/tick-history/tick-history-pcap)
frames the same as Levels I/II/III and lets clients retain lossless top-of-book **or**
full depth — depth retained is a choice.

## Why options push you toward top-of-book for the whole chain

- Options dominate market-data volume: on the order of **1.5 million option
  instruments vs ~10,000 stocks**, so options generate the majority of all market data
  ([Sherwood/Nasdaq](https://sherwood.news/sponsored/whats-driving-the-growth-in-options-trading/)).
  Full depth across the whole option universe is the biggest storage burden, so firms
  are selective.
- Exchange depth for index options is often shallow — CME's market-depth files carry
  **equity index options at only 3 levels**
  ([CME Datamine FAQ](https://www.cmegroup.com/market-data/files/cme-group-market-depth-faq.pdf)).
  NSE's standard feed (Zerodha `full` mode) is **5 levels**; 20-level is a premium feed.
- Option-chain analytics (OI, PCR, max-pain, IV/Greek surfaces) use **top-of-book mid
  prices + OI + volume**, not deep books. Deep book matters for *execution*
  ([TradeAlgo on L2 options](https://www.tradealgo.com/trading-guides/options/level-2-options-data-understanding-market-depth-for-better-options-execution)).

## The prevailing professional pattern

- **Top-of-book (L1) for the entire option universe**, retained broadly.
- **Full depth (L2/MBP) only for a focused subset** — instruments actually traded or
  where execution quality is measured.
- **Order-by-order (L3/MBO)** kept mainly by market makers for what they quote.
- Firms with budget (or vendor feeds) may keep full depth on everything — an
  infra/cost choice, not an analytical necessity.

## Our decision

- **Index option chain → L1.** Matches the professional norm and `algo_engine`; top-5
  across 202 legs/index multiplies data for little analytical gain on a chain.
- **Stocks / calendar-spread book → L5.** Small instrument count, and this is the
  execution/liquidity use case where depth pays off; cheap at 1 Hz.

See [[bin-format]] and [[stocks-capture]] for how each is stored.
