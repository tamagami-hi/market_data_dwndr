import type { GridBlock, MarketHeaderPayload, OptionGridPayload } from "@/lib/wsTypes";

export const GRID_KEYS: readonly (keyof GridBlock)[] = [
  "oi",
  "change_in_oi",
  "volume",
  "iv",
  "delta",
  "gamma",
  "theta",
  "vega",
  "rho",
  "bid",
  "ask",
  "ltp",
  "change",
];

export interface OptionGrid {
  strikes: number[];
  calls: GridBlock;
  puts: GridBlock;
  spot: number;
  marketAtm: number;
  maxPain: number;
  spotAtm: number;
}

export interface OptionDelta {
  changedIndices: number[];
  calls: Partial<GridBlock>;
  puts: Partial<GridBlock>;
}

export interface NormalizedOptionDeltaPayload {
  underlying: string;
  changed_indices: number[];
  calls: Partial<GridBlock>;
  puts: Partial<GridBlock>;
}

export interface OptionRowModel {
  strike: number;
  call: Record<keyof GridBlock, number>;
  put: Record<keyof GridBlock, number>;
  isAtm: boolean;
  isMaxPain: boolean;
  isSpotAtm: boolean;
  isCallInTheMoney: boolean;
  isPutInTheMoney: boolean;
}

function isFiniteArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => typeof item === "number" && Number.isFinite(item));
}

function normalizeBlock(value: unknown, rowCount: number): GridBlock | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const entries = GRID_KEYS.map((key) => [key, source[key]] as const);
  if (entries.some(([, column]) => !isFiniteArray(column) || column.length < rowCount)) return null;
  return Object.fromEntries(entries) as unknown as GridBlock;
}

function normalizePartialBlock(value: unknown, rowCount: number): Partial<GridBlock> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  return GRID_KEYS.reduce<Partial<GridBlock> | null>((normalized, key) => {
    if (!normalized) return null;
    const column = source[key];
    if (column === undefined) return normalized;
    if (!isFiniteArray(column) || column.length < rowCount) return null;
    return { ...normalized, [key]: column };
  }, {});
}

export function normalizeOptionDeltaPayload(value: unknown): NormalizedOptionDeltaPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  if (
    typeof payload.underlying !== "string" ||
    payload.underlying.length === 0 ||
    !Array.isArray(payload.changed_indices) ||
    !payload.changed_indices.every(
      (index) => typeof index === "number" && Number.isInteger(index) && index >= 0,
    )
  ) {
    return null;
  }
  const calls = normalizePartialBlock(payload.calls, payload.changed_indices.length);
  const puts = normalizePartialBlock(payload.puts, payload.changed_indices.length);
  if (!calls || !puts) return null;
  return {
    underlying: payload.underlying,
    changed_indices: [...payload.changed_indices],
    calls,
    puts,
  };
}

export function normalizeOptionGridPayload(value: unknown): OptionGridPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  if (
    typeof payload.underlying !== "string" ||
    typeof payload.expiry !== "string" ||
    !isFiniteArray(payload.strikes)
  ) {
    return null;
  }
  const calls = normalizeBlock(payload.calls, payload.strikes.length);
  const puts = normalizeBlock(payload.puts, payload.strikes.length);
  const numberKeys = ["market_atm", "max_pain", "spot_atm", "spot", "vix"] as const;
  if (!calls || !puts || numberKeys.some((key) => typeof payload[key] !== "number" || !Number.isFinite(payload[key]))) {
    return null;
  }
  return {
    underlying: payload.underlying,
    expiry: payload.expiry,
    strikes: payload.strikes,
    calls,
    puts,
    market_atm: payload.market_atm as number,
    max_pain: payload.max_pain as number,
    spot_atm: payload.spot_atm as number,
    spot: payload.spot as number,
    vix: payload.vix as number,
  };
}

export function normalizeMarketHeader(value: unknown): MarketHeaderPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  const numberKeys = ["spot", "atm", "vix", "risk_free_rate", "timestamp", "sequence"] as const;
  if (
    typeof payload.underlying !== "string" ||
    typeof payload.expiry !== "string" ||
    numberKeys.some((key) => typeof payload[key] !== "number" || !Number.isFinite(payload[key]))
  ) {
    return null;
  }
  return payload as unknown as MarketHeaderPayload;
}

function patchBlock(
  base: GridBlock,
  patch: Partial<GridBlock>,
  changedIndices: number[],
): GridBlock {
  return GRID_KEYS.reduce<GridBlock>((next, key) => {
    const values = patch[key];
    if (!values) return next;
    const column = [...base[key]];
    changedIndices.forEach((strikeIndex, patchIndex) => {
      if (strikeIndex >= 0 && strikeIndex < column.length) {
        column[strikeIndex] = values[patchIndex] ?? column[strikeIndex];
      }
    });
    return { ...next, [key]: column };
  }, base);
}

export function applyOptionDelta(grid: OptionGrid, delta: OptionDelta): OptionGrid {
  const calls = Object.keys(delta.calls).length > 0
    ? patchBlock(grid.calls, delta.calls, delta.changedIndices)
    : grid.calls;
  const puts = Object.keys(delta.puts).length > 0
    ? patchBlock(grid.puts, delta.puts, delta.changedIndices)
    : grid.puts;
  if (calls === grid.calls && puts === grid.puts) return grid;
  return { ...grid, calls, puts };
}

function rowSide(block: GridBlock, index: number): Record<keyof GridBlock, number> {
  return Object.fromEntries(
    GRID_KEYS.map((key) => [key, block[key][index] ?? 0]),
  ) as Record<keyof GridBlock, number>;
}

function createOptionRow(grid: OptionGrid, index: number): OptionRowModel {
  const strike = grid.strikes[index];
  return {
    strike,
    call: rowSide(grid.calls, index),
    put: rowSide(grid.puts, index),
    isAtm: Math.abs(strike - grid.marketAtm) < 1,
    isMaxPain: Math.abs(strike - grid.maxPain) < 1,
    isSpotAtm: Math.abs(strike - grid.spotAtm) < 1,
    isCallInTheMoney: strike < grid.spot,
    isPutInTheMoney: strike > grid.spot,
  };
}

export function areOptionRowsEqual(left: OptionRowModel, right: OptionRowModel): boolean {
  if (
    left.strike !== right.strike ||
    left.isAtm !== right.isAtm ||
    left.isMaxPain !== right.isMaxPain ||
    left.isSpotAtm !== right.isSpotAtm ||
    left.isCallInTheMoney !== right.isCallInTheMoney ||
    left.isPutInTheMoney !== right.isPutInTheMoney
  ) {
    return false;
  }
  return GRID_KEYS.every(
    (key) => left.call[key] === right.call[key] && left.put[key] === right.put[key],
  );
}

export function optionGridToRows(
  grid: OptionGrid,
  previous: OptionRowModel[] = [],
): OptionRowModel[] {
  return grid.strikes.map((_, index) => {
    const next = createOptionRow(grid, index);
    const prior = previous[index];
    return prior && areOptionRowsEqual(next, prior) ? prior : next;
  });
}
