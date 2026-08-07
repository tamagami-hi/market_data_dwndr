import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { StorageHistory } from "@/components/monitor/StorageHistory";
import type { CaptureHistory } from "@/lib/api";

function history(sessionCount: number): CaptureHistory {
  return {
    available: true,
    generated_at: 1,
    totals: {
      sessions: sessionCount,
      total_bytes: sessionCount * 1_000,
      raw_bytes: sessionCount * 500,
      archived_bytes: sessionCount * 500,
      data_files: sessionCount * 2,
    },
    sessions: Array.from({ length: sessionCount }, (_, index) => ({
      trading_date: `2026-07-${String(29 - index).padStart(2, "0")}`,
      is_current: index === 0,
      total_bytes: 1_000,
      raw_bytes: 500,
      archived_bytes: 500,
      data_files: 2,
      raw_files: 1,
      archived_files: 1,
      index_files: 1,
      stock_files: 1,
      indices: ["NIFTY"],
    })),
  };
}

test("caps the session table at six rows with a vertical scroll region", () => {
  render(<StorageHistory history={history(9)} />);

  const panel = screen
    .getByRole("heading", { name: "Download history" })
    .closest(".panel") as HTMLElement;
  const scrollRegion = panel.querySelector(".monitor-storage-scroll");
  expect(scrollRegion).not.toBeNull();
  expect(scrollRegion).toHaveClass("overflow-auto");
  // All nine sessions stay in the DOM; the CSS max-height, not truncation,
  // keeps six rows visible and scrolls the rest.
  expect(panel.querySelectorAll("tbody tr")).toHaveLength(9);
});
