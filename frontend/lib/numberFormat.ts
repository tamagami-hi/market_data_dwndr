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

function formatIndianCompact(value: number, maxDecimals = 1): string {
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

/** Percentage with a trailing "%". ``value`` is already in percent (0..100). */
export function formatPercent(value: number | null | undefined, decimals = 1): string {
  if (value == null || Number.isNaN(value)) return "--";
  return `${formatIndianNumber(value, decimals)}%`;
}

/** MB/s throughput (input is already MB/s). */
export function formatThroughput(mbps: number | null | undefined, decimals = 1): string {
  if (mbps == null || Number.isNaN(mbps)) return "--";
  return `${formatIndianNumber(mbps, decimals)} MB/s`;
}

/**
 * Human-readable duration from milliseconds.
 * < 1s -> "850 ms"; < 60s -> "12.3 s"; < 60m -> "3m 05s"; else "1h 02m".
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return "--";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  if (minutes < 60) return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return `${hours}h ${mins.toString().padStart(2, "0")}m`;
}

/** Compact uptime clock from ms: "HH:MM:SS" (hours can exceed 24). */
export function formatUptime(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return "--:--:--";
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

/**
 * Whole seconds as a compact duration: "0s", "54s", "1m 38s", "53m 04s", "2h 05m 09s".
 *
 * Used where the unit is already seconds (loss accounting), so a 54-second stale spell
 * reads "54s" rather than formatDuration's "54.0 s".
 */
export function formatSecondsCompact(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return "--";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  if (hours > 0) return `${hours}h ${pad(minutes)}m ${pad(secs)}s`;
  if (minutes > 0) return `${minutes}m ${pad(secs)}s`;
  return `${secs}s`;
}

/**
 * Local date and time, for values that may not be from today: "2026-11-06 15:40:00", or
 * "11-06 15:40" when ``short`` (dense table cells).
 */
export function formatDateClock(ms: number | null | undefined, short = false): string {
  if (!ms) return "--";
  const date = new Date(ms);
  const pad = (n: number) => n.toString().padStart(2, "0");
  const day = `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (short) return `${day} ${clock}`;
  return `${date.getFullYear()}-${day} ${clock}:${pad(date.getSeconds())}`;
}

const istClockFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/**
 * Exchange-local (IST) clock time, "10:23:00", whatever the viewer's timezone: incident
 * times are compared against the exchange schedule, not the operator's laptop clock.
 */
export function formatIstClock(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return "--";
  return istClockFormatter.format(new Date(ms));
}
