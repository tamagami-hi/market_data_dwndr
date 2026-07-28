import { expect, test } from "vitest";

import {
  formatBytes,
  formatDuration,
  formatPercent,
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
