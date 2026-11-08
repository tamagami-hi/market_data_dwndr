/** Narrowing helpers shared by the monitor payload normalizers. Inputs are untrusted JSON. */

export type UnknownRecord = Record<string, unknown>;

export function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

export function finite(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function optionalFinite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function optionalText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/**
 * The subset of ``keys`` that hold finite numbers, as a new object. Absent or invalid
 * keys stay absent (not 0), so callers can tell "not recorded" from "recorded as zero".
 */
export function pickFinite<K extends string>(
  record: UnknownRecord,
  keys: readonly K[],
): Partial<Record<K, number>> {
  return Object.fromEntries(
    keys.flatMap((key) => {
      const value = optionalFinite(record[key]);
      return value === null ? [] : [[key, value]];
    }),
  ) as Partial<Record<K, number>>;
}
