import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import StockDepthPanel, { formatDepthCount } from "@/components/StockDepthPanel";
import type { DepthLevel, StockDepthSnapshot } from "@/lib/wsTypes";

function levels(base: number): DepthLevel[] {
  return Array.from({ length: 5 }, (_, index) => ({
    level: index + 1,
    bid_price: base - index * 0.05,
    bid_qty: 100 + index,
    bid_orders: 10 + index,
    ask_price: base + 0.1 + index * 0.05,
    ask_qty: 200 + index,
    ask_orders: 20 + index,
  }));
}

const DEPTH: StockDepthSnapshot = {
  tradingsymbol: "RELIANCE",
  name: "Reliance Industries",
  spot_depth: levels(2_450),
  futures: [
    { label: "Current future", expiry: "2026-07-30", depth: levels(2_460) },
    { label: "Mid future", expiry: "2026-08-27", depth: levels(2_470) },
    { label: "Far future", expiry: "2026-09-24", depth: levels(2_480) },
  ],
};

test("renders every L1-L5 leg as a symmetric seven-column bid and ask table", () => {
  render(<StockDepthPanel depth={DEPTH} id="reliance-depth" />);

  const tables = screen.getAllByRole("table");
  expect(tables).toHaveLength(4);

  tables.forEach((table) => {
    expect(table.querySelectorAll(":scope > colgroup")).toHaveLength(3);
    expect(table).toHaveClass("table-fixed");
    expect(table).not.toHaveClass("min-w-[28rem]");
    expect(table.parentElement).not.toHaveAttribute("tabindex");
    expect(table.parentElement).not.toHaveClass("overflow-x-auto");
    const rows = within(table).getAllByRole("row");
    expect(within(rows[0]).getByRole("columnheader", { name: "Bid" })).toHaveAttribute("colspan", "3");
    expect(within(rows[0]).getByRole("columnheader", { name: "Ask" })).toHaveAttribute("colspan", "3");
    expect(
      within(rows[1]).getAllByRole("columnheader").map((header) => header.getAttribute("aria-label")),
    ).toEqual([
      "Bid orders",
      "Bid qty",
      "Bid price",
      "Ask price",
      "Ask qty",
      "Ask orders",
    ]);

    const depthRows = rows.slice(2);
    expect(depthRows).toHaveLength(5);
    depthRows.forEach((row) => expect(within(row).getAllByRole("cell")).toHaveLength(7));
  });
});

test("keeps a visible status while live order-book depth is unavailable", () => {
  render(<StockDepthPanel depth={null} id="pending-depth" />);

  expect(screen.getByRole("status")).toHaveTextContent("Waiting for the live order book.");
});

test("compacts large order-book counts without hiding their exact accessible value", () => {
  expect(formatDepthCount(12_345)).toEqual({ display: "12.3K", exact: "12,345" });
  expect(formatDepthCount(234_567)).toEqual({ display: "2.3L", exact: "2,34,567" });
  expect(formatDepthCount(16_482_987)).toEqual({ display: "1.6Cr", exact: "1,64,82,987" });
});
