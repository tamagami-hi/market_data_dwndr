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

test("tints and labels only the sessions that lost more than 1%", () => {
  const base = history(4);
  const losses = [13.784, 0.463, 2.152, null];
  const withLoss: CaptureHistory = {
    ...base,
    sessions: base.sessions.map((session, index) => ({
      ...session,
      loss_pct: losses[index],
      outcome: index === 0 ? "partial" : null,
    })),
  };
  const { container } = render(<StorageHistory history={withLoss} />);
  const rows = Array.from(container.querySelectorAll("tbody tr")) as HTMLTableRowElement[];

  expect(rows[0].textContent).toContain("13.78%");
  expect(rows[0].getAttribute("title")).toBe("capture loss 13.78% (partial)");
  expect(rows[0].style.boxShadow).toContain("inset 3px 0 0");
  expect(rows[1].textContent).toContain("0.46%");
  expect(rows[1].style.boxShadow).toBe("");
  expect(rows[2].style.boxShadow).toContain("inset 3px 0 0");
  expect(rows[3].textContent).toContain("--");
  expect(rows[3].getAttribute("title")).toBe("no capture session recorded for this date");
  expect(screen.getAllByText(/tinted by capture loss above 1%/).length).toBeGreaterThan(0);
});

test("marks days with mislabelled strikes in violet, separately from the loss tint", () => {
  const base = history(3);
  const marked: CaptureHistory = {
    ...base,
    sessions: base.sessions.map((session, index) => ({
      ...session,
      loss_pct: index === 1 ? 13.784 : 0.2,
      mislabelled_frames: index === 2 ? 0 : 22_799,
      mislabelled_artifacts: index === 2 ? [] : ["NIFTY", "SENSEX"],
    })),
  };
  const { container } = render(<StorageHistory history={marked} />);
  const rows = Array.from(container.querySelectorAll("tbody tr")) as HTMLTableRowElement[];

  // Mislabelled only: violet right-edge bar, no loss tint.
  expect(rows[0].style.boxShadow).toBe("inset -3px 0 0 var(--mislabelled)");
  expect(rows[0].textContent).toContain("strikes mislabelled: 22,799 index frames read as missing (NIFTY, SENSEX)");
  // Both: the loss bar on the left and the violet bar on the right.
  expect(rows[1].style.boxShadow).toContain("inset 3px 0 0");
  expect(rows[1].style.boxShadow).toContain("inset -3px 0 0 var(--mislabelled)");
  expect(rows[1].getAttribute("title")).toContain("capture loss 13.78%");
  expect(rows[1].getAttribute("title")).toContain("strikes mislabelled");
  // Neither.
  expect(rows[2].style.boxShadow).toBe("");
  expect(rows[2].textContent).not.toContain("mislabelled");
});
