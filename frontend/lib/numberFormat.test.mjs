import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("./numberFormat.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { formatPercent, formatThroughput, formatDuration, formatUptime, formatBytes } =
  await import(moduleUrl);

test("formatPercent appends % and handles nullish", () => {
  assert.equal(formatPercent(12.5, 1), "12.5%");
  assert.equal(formatPercent(0, 0), "0%");
  assert.equal(formatPercent(null), "--");
  assert.equal(formatPercent(Number.NaN), "--");
});

test("formatThroughput renders MB/s", () => {
  assert.equal(formatThroughput(42.34, 1), "42.3 MB/s");
  assert.equal(formatThroughput(null), "--");
});

test("formatDuration scales by magnitude", () => {
  assert.equal(formatDuration(850), "850 ms");
  assert.equal(formatDuration(12_340), "12.3 s");
  assert.equal(formatDuration(185_000), "3m 05s");
  assert.equal(formatDuration(3_720_000), "1h 02m");
  assert.equal(formatDuration(-1), "--");
  assert.equal(formatDuration(null), "--");
});

test("formatUptime renders HH:MM:SS clock", () => {
  assert.equal(formatUptime(0), "00:00:00");
  assert.equal(formatUptime(61_000), "00:01:01");
  assert.equal(formatUptime(3_661_000), "01:01:01");
  assert.equal(formatUptime(null), "--:--:--");
});

test("formatBytes still works", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1024), "1.0 KB");
});
