import { expect, test } from "vitest";

import {
  formatBytes,
  formatDateClock,
  formatDuration,
  formatIstClock,
  formatPercent,
  formatSecondsCompact,
  formatThroughput,
  formatUptime,
} from "@/lib/numberFormat";

test("formatPercent appends % and handles nullish", () => {
  expect(formatPercent(12.5, 1)).toBe("12.5%");
  expect(formatPercent(0, 0)).toBe("0%");
  expect(formatPercent(null)).toBe("--");
  expect(formatPercent(Number.NaN)).toBe("--");
});

test("formatThroughput renders MB/s", () => {
  expect(formatThroughput(42.34, 1)).toBe("42.3 MB/s");
  expect(formatThroughput(null)).toBe("--");
});

test("formatDuration scales by magnitude", () => {
  expect(formatDuration(850)).toBe("850 ms");
  expect(formatDuration(12_340)).toBe("12.3 s");
  expect(formatDuration(185_000)).toBe("3m 05s");
  expect(formatDuration(3_720_000)).toBe("1h 02m");
  expect(formatDuration(-1)).toBe("--");
  expect(formatDuration(null)).toBe("--");
});

test("formatUptime renders HH:MM:SS clock", () => {
  expect(formatUptime(0)).toBe("00:00:00");
  expect(formatUptime(61_000)).toBe("00:01:01");
  expect(formatUptime(3_661_000)).toBe("01:01:01");
  expect(formatUptime(null)).toBe("--:--:--");
});

test("formatBytes still works", () => {
  expect(formatBytes(0)).toBe("0 B");
  expect(formatBytes(1024)).toBe("1.0 KB");
});

test("formatSecondsCompact keeps whole seconds readable at every scale", () => {
  expect(formatSecondsCompact(0)).toBe("0s");
  expect(formatSecondsCompact(54)).toBe("54s");
  expect(formatSecondsCompact(98)).toBe("1m 38s");
  expect(formatSecondsCompact(3_184)).toBe("53m 04s");
  expect(formatSecondsCompact(7_509)).toBe("2h 05m 09s");
  expect(formatSecondsCompact(-1)).toBe("--");
  expect(formatSecondsCompact(null)).toBe("--");
  expect(formatSecondsCompact(Number.NaN)).toBe("--");
});

test("formatDateClock includes the date, in full or short form", () => {
  const ms = new Date(2026, 10, 6, 15, 40, 0).getTime();
  expect(formatDateClock(ms)).toBe("2026-11-06 15:40:00");
  expect(formatDateClock(ms, true)).toBe("11-06 15:40");
  expect(formatDateClock(null)).toBe("--");
});

test("formatIstClock renders exchange time whatever the viewer's timezone", () => {
  // 2026-11-05 10:23:00 IST = 04:53:00 UTC.
  expect(formatIstClock(Date.UTC(2026, 10, 5, 4, 53, 0))).toBe("10:23:00");
  expect(formatIstClock(null)).toBe("--");
  expect(formatIstClock(0)).toBe("--");
});
