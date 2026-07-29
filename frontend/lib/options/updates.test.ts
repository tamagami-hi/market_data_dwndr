import { describe, expect, test } from "vitest";

import {
  applyOptionDelta,
  areOptionRowsEqual,
  normalizeMarketHeader,
  normalizeOptionDeltaPayload,
  normalizeOptionGridPayload,
  optionGridToRows,
  type OptionGrid,
} from "@/lib/options/updates";
import type { GridBlock } from "@/lib/wsTypes";

const block = (offset: number): GridBlock => ({
  oi: [1 + offset, 2 + offset],
  change_in_oi: [3 + offset, 4 + offset],
  volume: [5 + offset, 6 + offset],
  iv: [7 + offset, 8 + offset],
  delta: [9 + offset, 10 + offset],
  gamma: [11 + offset, 12 + offset],
  theta: [13 + offset, 14 + offset],
  vega: [15 + offset, 16 + offset],
  rho: [17 + offset, 18 + offset],
  bid: [19 + offset, 20 + offset],
  ask: [21 + offset, 22 + offset],
  ltp: [23 + offset, 24 + offset],
  change: [25 + offset, 26 + offset],
});

function wideBlock(rowCount: number): GridBlock {
  return Object.fromEntries(
    Object.keys(block(0)).map((key) => [
      key,
      Array.from({ length: rowCount }, (_, index) => index + 1),
    ]),
  ) as unknown as GridBlock;
}

const grid: OptionGrid = {
  strikes: [22_000, 22_050],
  calls: block(0),
  puts: block(100),
  spot: 22_010,
  marketAtm: 22_000,
  maxPain: 22_050,
  spotAtm: 22_000,
};

describe("option updates", () => {
  test("copies only patched columns and leaves the input immutable", () => {
    const next = applyOptionDelta(grid, {
      changedIndices: [1],
      calls: { ltp: [999] },
      puts: {},
    });

    expect(next).not.toBe(grid);
    expect(next.calls).not.toBe(grid.calls);
    expect(next.calls.ltp).not.toBe(grid.calls.ltp);
    expect(next.calls.oi).toBe(grid.calls.oi);
    expect(next.puts).toBe(grid.puts);
    expect(next.calls.ltp).toEqual([23, 999]);
    expect(grid.calls.ltp).toEqual([23, 24]);
  });

  test("reuses unchanged row models during one hertz snapshots", () => {
    const previous = optionGridToRows(grid);
    const same = optionGridToRows({ ...grid }, previous);
    expect(same[0]).toBe(previous[0]);
    expect(same[1]).toBe(previous[1]);

    const changedGrid = applyOptionDelta(grid, {
      changedIndices: [1],
      calls: { ltp: [999] },
      puts: {},
    });
    const changed = optionGridToRows(changedGrid, previous);
    expect(changed[0]).toBe(previous[0]);
    expect(changed[1]).not.toBe(previous[1]);
    expect(areOptionRowsEqual(changed[0], previous[0])).toBe(true);
  });

  test("validates complete grid and header payloads at the stream boundary", () => {
    expect(
      normalizeOptionGridPayload({
        underlying: "NIFTY",
        expiry: "2026-07-30",
        strikes: grid.strikes,
        calls: grid.calls,
        puts: grid.puts,
        market_atm: grid.marketAtm,
        max_pain: grid.maxPain,
        spot_atm: grid.spotAtm,
        spot: grid.spot,
        vix: 12,
      }),
    ).not.toBeNull();
    expect(normalizeOptionGridPayload({ underlying: "NIFTY", strikes: [1], calls: {} })).toBeNull();
    expect(
      normalizeMarketHeader({
        underlying: "NIFTY",
        expiry: "2026-07-30",
        spot: 22_000,
        atm: 22_000,
        vix: 12,
        risk_free_rate: 0.06,
        timestamp: 1,
        sequence: 2,
      }),
    ).not.toBeNull();
    expect(normalizeMarketHeader({ underlying: 42 })).toBeNull();
    expect(normalizeOptionDeltaPayload({ underlying: "NIFTY", changed_indices: "bad" })).toBeNull();
    expect(
      normalizeOptionDeltaPayload({
        underlying: "NIFTY",
        changed_indices: [0],
        calls: { ltp: [1] },
        puts: {},
      })?.changed_indices,
    ).toEqual([0]);
  });

  test("preserves every supplied strike above and below spot ATM", () => {
    const strikes = Array.from({ length: 101 }, (_, index) => 19_500 + index * 50);
    const normalized = normalizeOptionGridPayload({
      underlying: "NIFTY",
      expiry: "2026-07-30",
      strikes,
      calls: wideBlock(strikes.length),
      puts: wideBlock(strikes.length),
      market_atm: 22_000,
      max_pain: 22_050,
      spot_atm: 22_000,
      spot: 22_010,
      vix: 12,
    });
    expect(normalized).not.toBeNull();

    const rows = optionGridToRows({
      strikes: normalized!.strikes,
      calls: normalized!.calls,
      puts: normalized!.puts,
      spot: normalized!.spot,
      marketAtm: normalized!.market_atm,
      maxPain: normalized!.max_pain,
      spotAtm: normalized!.spot_atm,
    });
    expect(rows).toHaveLength(101);
    expect(rows[0].strike).toBe(19_500);
    expect(rows.at(-1)?.strike).toBe(24_500);
    expect(rows.filter(({ strike }) => strike < 22_000)).toHaveLength(50);
    expect(rows.filter(({ strike }) => strike > 22_000)).toHaveLength(50);
  });

  test("ignores invalid delta indices and invalidates marker changes", () => {
    const unchanged = applyOptionDelta(grid, {
      changedIndices: [-1, 99],
      calls: { ltp: [500, 600] },
      puts: {},
    });
    expect(unchanged.calls.ltp).toEqual(grid.calls.ltp);

    const previous = optionGridToRows(grid);
    const movedMarkers = optionGridToRows({ ...grid, marketAtm: 22_050 }, previous);
    expect(movedMarkers[0]).not.toBe(previous[0]);
    expect(movedMarkers[1]).not.toBe(previous[1]);
  });
});
