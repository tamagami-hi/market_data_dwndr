import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import OptionChainTable, {
  optionMarkerCode,
  optionMarkerVariant,
} from "@/components/OptionChainTable";
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
  expect(container.querySelector("tbody td.sticky")).toHaveTextContent("S-A-M");
  expect(container.querySelector("table")).toHaveClass("table-fixed");
  const columns = [...container.querySelectorAll<HTMLTableColElement>("colgroup col")];
  expect(columns).toHaveLength(27);
  expect(columns.every((column) => column.style.width.endsWith("%"))).toBe(true);
  expect(
    columns.reduce((sum, column) => sum + Number.parseFloat(column.style.width), 0),
  ).toBeCloseTo(100, 3);
  const frame = container.querySelector("[data-option-table-frame]");
  expect(frame).toHaveClass("overflow-y-auto", "overflow-x-hidden");
  expect(frame).not.toHaveClass("overflow-auto");
  expect(container.querySelector("table")).toHaveStyle({ width: "100%" });
  expect(container.querySelector("table")).not.toHaveStyle({ minWidth: "2386px" });
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
  expect(screen.getByText("SP")).toBeInTheDocument();
  expect(screen.getByText("ATM")).toBeInTheDocument();
  expect(screen.getByText("MP")).toBeInTheDocument();
  expect(container.querySelector(".text-danger")).toBeInTheDocument();
  expect(container.querySelector(".text-success")).toBeInTheDocument();
});

test("compresses every option marker collision without losing its meaning", () => {
  expect(optionMarkerCode({ isSpotAtm: false, isAtm: false, isMaxPain: false })).toBeNull();
  expect(optionMarkerCode({ isSpotAtm: true, isAtm: false, isMaxPain: false })).toBe("SP");
  expect(optionMarkerCode({ isSpotAtm: false, isAtm: true, isMaxPain: false })).toBe("ATM");
  expect(optionMarkerCode({ isSpotAtm: false, isAtm: false, isMaxPain: true })).toBe("MP");
  expect(optionMarkerCode({ isSpotAtm: true, isAtm: true, isMaxPain: false })).toBe("SA");
  expect(optionMarkerCode({ isSpotAtm: true, isAtm: false, isMaxPain: true })).toBe("SM");
  expect(optionMarkerCode({ isSpotAtm: false, isAtm: true, isMaxPain: true })).toBe("AM");
  expect(optionMarkerCode({ isSpotAtm: true, isAtm: true, isMaxPain: true })).toBe("S-A-M");
});

test("assigns a distinct visual variant to every marker combination", () => {
  const flags = [
    { isSpotAtm: true, isAtm: false, isMaxPain: false },
    { isSpotAtm: false, isAtm: true, isMaxPain: false },
    { isSpotAtm: false, isAtm: false, isMaxPain: true },
    { isSpotAtm: true, isAtm: true, isMaxPain: false },
    { isSpotAtm: true, isAtm: false, isMaxPain: true },
    { isSpotAtm: false, isAtm: true, isMaxPain: true },
    { isSpotAtm: true, isAtm: true, isMaxPain: true },
  ];

  expect(flags.map(optionMarkerVariant)).toEqual([
    "spot",
    "atm",
    "pain",
    "spot-atm",
    "spot-pain",
    "atm-pain",
    "spot-atm-pain",
  ]);
  expect(new Set(flags.map(optionMarkerVariant))).toHaveLength(7);
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
