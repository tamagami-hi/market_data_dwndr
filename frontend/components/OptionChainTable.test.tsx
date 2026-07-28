import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import OptionChainTable from "@/components/OptionChainTable";
import type { GridBlock } from "@/lib/wsTypes";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        key: index,
        start: index * 30,
        end: (index + 1) * 30,
        size: 30,
      })),
    getTotalSize: () => count * 30,
  }),
}));

function block(offset: number): GridBlock {
  return {
    oi: [1 + offset],
    change_in_oi: [2 + offset],
    volume: [3 + offset],
    iv: [4 + offset],
    delta: [5 + offset],
    gamma: [6 + offset],
    theta: [7 + offset],
    vega: [8 + offset],
    rho: [9 + offset],
    bid: [10 + offset],
    ask: [11 + offset],
    ltp: [12 + offset],
    change: [13 + offset],
  };
}

test("keeps a sticky strike identity on desktop", () => {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    media: "(min-width: 1024px)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
  const { container } = render(
    <OptionChainTable
      data={{
        strikes: [22_000],
        calls: block(0),
        puts: block(100),
        spot: 22_010,
        marketAtm: 22_000,
        maxPain: 22_000,
        spotAtm: 22_000,
      }}
    />,
  );
  expect(container.querySelector("th.sticky")).toHaveTextContent("STRIKE");
  expect(screen.queryByRole("columnheader", { name: "MARKERS" })).not.toBeInTheDocument();
  expect(container.querySelector("tbody td.sticky")).toHaveTextContent("SPOT");
});

test("renders virtualized desktop row tones, markers, and signed changes", () => {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    media: "(min-width: 1024px)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
  const columns = (offset: number): GridBlock => Object.fromEntries(
    Object.keys(block(0)).map((key, keyIndex) => [
      key,
      [0, 1, 2, 3].map((value) =>
        key === "change" || key === "change_in_oi"
          ? [-1, 0, 1, 2][value]
          : offset + keyIndex + value,
      ),
    ]),
  ) as unknown as GridBlock;
  const { container } = render(
    <OptionChainTable
      data={{
        strikes: [21_950, 22_000, 22_050, 22_100],
        calls: columns(0),
        puts: columns(100),
        spot: 22_025,
        marketAtm: 21_950,
        maxPain: 22_000,
        spotAtm: 22_050,
      }}
    />,
  );

  expect(container.querySelectorAll("tbody tr")).toHaveLength(4);
  expect(screen.getByText("SPOT")).toBeInTheDocument();
  expect(screen.getByText("ATM")).toBeInTheDocument();
  expect(screen.getByText("PAIN")).toBeInTheDocument();
  expect(container.querySelector(".text-danger")).toBeInTheDocument();
  expect(container.querySelector(".text-success")).toBeInTheDocument();
});

test("exposes every mobile field group", async () => {
  const user = userEvent.setup();
  render(
    <OptionChainTable
      data={{
        strikes: [22_000],
        calls: block(0),
        puts: block(100),
        spot: 22_010,
        marketAtm: 22_000,
        maxPain: 22_000,
        spotAtm: 22_000,
      }}
    />,
  );

  const disclosure = screen.getByRole("button", { name: "Strike 22000 details" });
  await user.click(disclosure);
  expect(screen.getByRole("heading", { name: "Price" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Flow" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Greeks" })).toBeVisible();
  expect(screen.getAllByText("Rho/1%").length).toBeGreaterThan(0);
});
