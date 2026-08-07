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



// --- dynamic artifact list ----------------------------------------------------
//
// §22: the dashboard must not assume TickVault has exactly five capture domains. Every row
// comes from the artifact list the backend sends, so a new domain appears with no change
// here — which is exactly how INDICES_FnO shows up.


function artifact(underlying: string, overrides: Partial<PerUnderlyingStatus> = {}) {
  return { ...ROW, underlying, ...overrides } as PerUnderlyingStatus;
}

test("renders however many artifacts the backend reports, including new domains", () => {
  const rows = ["NIFTY", "BANKNIFTY", "STOCKS", "INDICES_FnO"].map((name) =>
    artifact(name, { market_phase: "OPEN", capture_active: true }),
  );

  render(<UnderlyingHealth rows={rows} />);

  for (const name of ["NIFTY", "BANKNIFTY", "STOCKS", "INDICES_FnO"]) {
    expect(screen.getAllByText(name).length).toBeGreaterThan(0);
  }
  expect(screen.getByText("4 artifacts")).toBeInTheDocument();
});

test("a single artifact is labelled in the singular", () => {
  render(<UnderlyingHealth rows={[artifact("INDICES_FnO")]} />);
  expect(screen.getByText("1 artifact")).toBeInTheDocument();
});

test("each artifact shows its own session phase", () => {
  const rows = [
    artifact("NIFTY", { market_phase: "OPEN", capture_active: true }),
    artifact("INDICES_FnO", { market_phase: "CLOSED", capture_active: false }),
  ];

  render(<UnderlyingHealth rows={rows} />);

  // Both phases are rendered, so one artifact closing while another trades is visible.
  expect(screen.getAllByText("OPEN").length).toBeGreaterThan(0);
  expect(screen.getAllByText("CLOSED").length).toBeGreaterThan(0);
});

test("an artifact outside its session is muted rather than flagged as failing", () => {
  render(
    <UnderlyingHealth
      rows={[artifact("INDICES_FnO", { market_phase: "CLOSED", capture_active: false })]}
    />,
  );

  // Target the table cell specifically; the mobile disclosure renders the phase too.
  const cell = screen
    .getAllByText("CLSD")
    .map((element) => element.closest("td"))
    .find((element) => element !== null);
  expect(cell).toBeTruthy();
  expect(cell!.className).toContain("text-muted");
  expect(cell!.className).not.toContain("text-danger");
});
