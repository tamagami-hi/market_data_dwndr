"use client";

const numberFormatters = new Map<number, Intl.NumberFormat>();
const compactFormatters = new Map<number, Intl.NumberFormat>();

function getNumberFormatter(decimals: number): Intl.NumberFormat {
  let f = numberFormatters.get(decimals);
  if (!f) {
    f = new Intl.NumberFormat("en-IN", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    numberFormatters.set(decimals, f);
  }
  return f;
}

function getCompactFormatter(maxDecimals: number): Intl.NumberFormat {
  let f = compactFormatters.get(maxDecimals);
  if (!f) {
    f = new Intl.NumberFormat("en-IN", {
      notation: "compact",
      maximumFractionDigits: maxDecimals,
    });
    compactFormatters.set(maxDecimals, f);
  }
  return f;
}

export function formatIndianNumber(value: number, decimals = 2): string {
  return getNumberFormatter(decimals).format(value);
}

export function formatIndianCompact(value: number, maxDecimals = 1): string {
  return getCompactFormatter(maxDecimals).format(value);
}

/** Compact-aware cell formatter used across tables (dash for exact zero). */
export function fmtCell(val: number, decimals = 2): string {
  if (val === 0 || Number.isNaN(val)) return "-";
  if (Math.abs(val) >= 100000) return formatIndianCompact(val);
  const integerDigits = Math.abs(Math.trunc(val)).toString().length;
  const effective = decimals === 0 ? 0 : integerDigits >= 4 ? 1 : decimals;
  return formatIndianNumber(val, effective);
}

export function formatBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(n) / Math.log(1024));
  return `${(n / Math.pow(1024, i)).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function formatClockTime(ms: number | null | undefined): string {
  if (!ms) return "--";
  return new Date(ms).toLocaleTimeString();
}
