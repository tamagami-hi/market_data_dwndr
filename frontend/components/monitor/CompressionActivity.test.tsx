import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { CompressionActivity } from "@/components/monitor/CompressionActivity";
import type { CompressionHistory } from "@/lib/api";
import type { CompressionProgressPayload } from "@/lib/wsTypes";

const DONE: CompressionProgressPayload = {
  phase: "done",
  files_done: 8,
  files_total: 8,
  bytes_done: 9_495_610_201,
  bytes_total: 9_495_610_201,
  zst_bytes: 341_348_171,
  ratio: 27.82,
  current_file: null,
  threads: 6,
  started_at: new Date(2026, 10, 6, 15, 42, 0).getTime(),
  updated_at: 0,
  elapsed_ms: 441_428,
  file_elapsed_ms: 0,
  avg_file_ms: 55_178.2,
  throughput_mbps: 21.51,
};

const HISTORY: CompressionHistory = {
  samples: 102,
  avg_ratio: 23.324,
  avg_total_elapsed_ms: 293_633.3,
  avg_file_ms: 40_340.7,
  avg_throughput_mbps: 15.78,
  last: {
    trading_date: "2026-11-06",
    files: 8,
    raw_bytes: 9_495_610_201,
    zst_bytes: 341_348_171,
    ratio: 27.818,
    total_elapsed_ms: 441_428,
    avg_file_ms: 55_178.2,
    throughput_mbps: 21.51,
    threads: 6,
  },
};

function subtitle(): string {
  const heading = screen.getByRole("heading", { name: "Compression" });
  return heading.closest(".panel")?.querySelector(".panel-title-line")?.textContent ?? "";
}

test("a finished sweep names the session it compressed", () => {
  render(<CompressionActivity current={DONE} history={HISTORY} />);
  expect(subtitle()).toContain("done · 2026-11-06");
});

test("an unmatched finished sweep is dated by when it started", () => {
  render(<CompressionActivity current={DONE} history={{ ...HISTORY, last: null }} />);
  expect(subtitle()).toContain("done · started 11-06 15:42");
});

test("a running sweep keeps its phase", () => {
  render(<CompressionActivity current={{ ...DONE, phase: "compressing" }} history={HISTORY} />);
  expect(subtitle()).toContain("compressing");
});
