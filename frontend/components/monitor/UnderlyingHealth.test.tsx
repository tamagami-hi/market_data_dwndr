import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { UnderlyingHealth } from "@/components/monitor/UnderlyingHealth";
import type { PerUnderlyingStatus } from "@/lib/wsTypes";

const ROW: PerUnderlyingStatus = {
  underlying: "NIFTY",
  connected: true,
  last_tick_ms: 1,
  frames_written: 100,
  frames_expected: 110,
  frame_loss_pct: 9.09,
  session_frames_expected: 101,
  session_loss_pct: 1.25,
  day_complete_pct: 42,
  file_bytes: 1_000,
  avg_bytes_per_frame: 10,
  projected_eod_bytes: 2_000,
  heartbeat_ok: true,
  heartbeat_age_ms: 100,
  data_fresh: true,
  unmatched: 0,
  applied: 10,
  writer_pending: 0,
};

test("keeps elapsed loss visible without an explanatory question-mark control", () => {
  render(<UnderlyingHealth rows={[ROW]} />);

  expect(screen.getByRole("columnheader", { name: "Elapsed loss" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Explain elapsed loss" })).not.toBeInTheDocument();
  expect(screen.getAllByText("1.25%").length).toBeGreaterThan(0);
});

test("uses compact accessible connection dots in the desktop health matrix", () => {
  render(
    <UnderlyingHealth
      rows={[
        ROW,
        { ...ROW, underlying: "BANKNIFTY", connected: false },
      ]}
    />,
  );

  const table = screen.getByRole("table");
  const header = within(table).getByRole("columnheader", { name: "Connection" });
  const [, connectedRow, offlineRow] = within(table).getAllByRole("row");
  const connectedDot = within(connectedRow).getByRole("img", {
    name: "NIFTY connection: connected",
  });
  const offlineDot = within(offlineRow).getByRole("img", {
    name: "BANKNIFTY connection: offline",
  });

  expect(header).toHaveTextContent("Link");
  expect(connectedDot).toHaveClass("bg-success");
  expect(connectedDot).not.toHaveClass("border-2");
  expect(offlineDot).toHaveClass("border-2", "border-danger", "bg-transparent");
  expect(connectedRow.querySelectorAll("td")[1]).not.toHaveTextContent("connected");
  expect(offlineRow.querySelectorAll("td")[1]).not.toHaveTextContent("offline");
});

test("does not expose a horizontal scrollbar for the desktop health matrix", () => {
  render(<UnderlyingHealth rows={[ROW]} />);

  const frame = screen.getByRole("table").parentElement;

  expect(frame).toHaveClass("overflow-x-hidden", "overflow-y-auto");
  expect(frame).not.toHaveClass("overflow-auto");
});
