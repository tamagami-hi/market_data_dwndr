import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { SessionHistory } from "@/components/monitor/SessionHistory";
import type { SessionSummary } from "@/lib/api";
import { formatIndianNumber } from "@/lib/numberFormat";

function session(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    trading_date: "2026-07-28",
    recorded_at: 1,
    uptime_ms: 60_000,
    captures: 50,
    frames_written: 1_234,
    frames_expected: 1_300,
    frame_loss_pct: 0.5,
    session_frames_expected: 1_280,
    session_loss_pct: 0.4,
    grid_seconds_elapsed: 100,
    data_loss_pct: 0.6,
    grid_gaps: 1,
    grid_seconds_lost: 2,
    stale_seconds: 3,
    stale_events: 1,
    dropped_batches: 0,
    drop_rate_pct: 0,
    unmatched_ticks: 0,
    ticks_received: 6_000,
    reconnects: 0,
    token_refreshes: 1,
    exhausted: false,
    disk_bytes: 2_048,
    streams: [],
    ...overrides,
  };
}

function panelRows(): HTMLElement[] {
  const panel = screen
    .getByRole("heading", { name: "Session history" })
    .closest(".panel") as HTMLElement;
  return within(panel).getAllByRole("row") as HTMLElement[];
}

test("prepends the in-progress session and counts frames written to the bin files", () => {
  render(
    <SessionHistory
      sessions={[session({ trading_date: "2026-07-27", frames_written: 900 })]}
      liveSession={session({ trading_date: "2026-07-28", frames_written: 1_234 })}
    />,
  );

  const rows = panelRows();
  // Header row + live session + one recorded session.
  expect(rows).toHaveLength(3);
  expect(within(rows[1]).getByText("2026-07-28")).toBeInTheDocument();
  expect(within(rows[1]).getByText("live")).toBeInTheDocument();
  // The Frames column tracks frames actually written to the .bin files
  // (frames_written = 1,234), not the capture-batch count (captures = 50).
  const cells = within(rows[1]).getAllByRole("cell");
  expect(cells[1]).toHaveTextContent(formatIndianNumber(1_234, 0));
  expect(cells[1]).not.toHaveTextContent("50");
});

test("a finalized record for the live date replaces the live row instead of duplicating it", () => {
  render(
    <SessionHistory
      sessions={[session({ trading_date: "2026-07-28", frames_written: 2_000 })]}
      liveSession={session({ trading_date: "2026-07-28", frames_written: 1_234 })}
    />,
  );

  const rows = panelRows();
  expect(rows).toHaveLength(2);
  expect(within(rows[1]).getByText("live")).toBeInTheDocument();
});

test("keeps the empty state until a recorded or live session exists", () => {
  const view = render(<SessionHistory sessions={[]} />);
  expect(screen.getByText("No completed sessions")).toBeInTheDocument();

  view.rerender(<SessionHistory sessions={[]} liveSession={session()} />);
  expect(screen.queryByText("No completed sessions")).not.toBeInTheDocument();
  expect(screen.getAllByText("2026-07-28").length).toBeGreaterThan(0);
});
