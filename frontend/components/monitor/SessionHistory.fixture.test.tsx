import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { SessionHistory } from "@/components/monitor/SessionHistory";
import type { SessionSummary } from "@/lib/api";
import { normalizeSessionSummary } from "@/lib/monitor/sessionSummary";

// Verbatim production records from /api/stats session_history (captured 2026-11-08).
//
// 2026-11-05: network outage. Capture ran 09:10–10:23, then crash-looped for ~51 minutes,
// then ran to the close with one more ~1 minute restart. 3,184 s missing = 54 stale feed +
// 98 process restart + 3,032 not running.
const RECORD_2026_11_05 = {
  trading_date: "2026-11-05", recorded_at: 1793873524379, uptime_ms: 23224346, captures: 19916,
  frames_written: 159328, frames_expected: 184800, frame_loss_pct: 13.784,
  session_frames_expected: 23046, session_loss_pct: 13.582, grid_seconds_elapsed: 23100,
  data_loss_pct: 13.784, grid_gaps: 2, grid_seconds_lost: 0, stale_seconds: 54, stale_events: 2,
  dropped_batches: 0, drop_rate_pct: 0.0, unmatched_ticks: 0, ticks_received: 4718363,
  reconnects: 0, token_refreshes: 0, longest_stale_spell_seconds: 25, escalations: 0,
  recovery_abandoned: false, exhausted: false, scheduled_seconds: 23100,
  scheduled_seconds_elapsed: 23100, captured_seconds: 19916, scheduled_loss_pct: 13.784,
  missing_seconds: 3184, stale_feed_seconds: 54, downtime_seconds: 3130, write_path_seconds: 0,
  unclassified_seconds: 0, awaiting_first_tick_seconds: 0, restart_downtime_seconds: 98,
  not_running_seconds: 3032, in_process_reconnects: 0, ws_connect_attempts: 1,
  disk_bytes: 8328183341,
  streams: [
    { underlying: "NIFTY", frames_written: 19916, frame_loss_pct: 13.784, file_bytes: 429203813 },
    { underlying: "STOCKS", frames_written: 19916, frame_loss_pct: 13.784, file_bytes: 5028900128 },
  ],
  generations: 2,
};

// 2026-10-19: a late start. 497 s missing (496 not running + 1 awaiting first tick), which
// the process-relative data_loss_pct (0.004%) cannot see. The page used to show 0.00%.
const RECORD_2026_10_19 = {
  trading_date: "2026-10-19", recorded_at: 1792491121322, uptime_ms: 109125847, captures: 22603,
  frames_written: 180824, frames_expected: 184800, frame_loss_pct: 2.152,
  session_frames_expected: 22603, session_loss_pct: 0.0, grid_seconds_elapsed: 22604,
  data_loss_pct: 0.004, grid_gaps: 0, grid_seconds_lost: 0, stale_seconds: 0, stale_events: 0,
  dropped_batches: 0, drop_rate_pct: 0.0, unmatched_ticks: 0, ticks_received: 22413668,
  reconnects: 0, token_refreshes: 0, longest_stale_spell_seconds: 0, escalations: 0,
  recovery_abandoned: false, exhausted: false, scheduled_seconds: 23100,
  scheduled_seconds_elapsed: 23100, captured_seconds: 22603, scheduled_loss_pct: 2.152,
  missing_seconds: 497, stale_feed_seconds: 0, downtime_seconds: 496, write_path_seconds: 0,
  unclassified_seconds: 0, awaiting_first_tick_seconds: 1, restart_downtime_seconds: 0,
  not_running_seconds: 496, in_process_reconnects: 0, ws_connect_attempts: 1,
  disk_bytes: 9399330977,
  streams: [{ underlying: "NIFTY", frames_written: 22603, frame_loss_pct: 2.152, file_bytes: 0 }],
  generations: 3,
};

function sessions(): SessionSummary[] {
  return [RECORD_2026_11_05, RECORD_2026_10_19].map((raw) => normalizeSessionSummary(raw)!);
}

function desktopRow(date: string): HTMLElement {
  const table = screen.getByRole("table");
  const button = within(table).getByRole("button", { name: date });
  return button.closest("tr") as HTMLElement;
}

test("the normalizer keeps the scheduled breakdown the history needs", () => {
  const [nov5] = sessions();
  expect(nov5.scheduled_loss_pct).toBe(13.784);
  expect(nov5.missing_seconds).toBe(3184);
  expect(nov5.restart_downtime_seconds).toBe(98);
  expect(nov5.not_running_seconds).toBe(3032);
  expect(nov5.ws_connect_attempts).toBe(1);
  expect(nov5.generations).toBe(2);
});

test("2026-11-05 shows schedule loss, missing time and its three causes", () => {
  render(<SessionHistory sessions={sessions()} />);
  const cells = within(desktopRow("2026-11-05")).getAllByRole("cell");

  // Session | Frames | Loss vs schedule | Missing | …
  expect(cells[2]).toHaveTextContent("13.78%");
  expect(cells[2].querySelector("span")).toHaveClass("text-danger");
  expect(cells[3]).toHaveTextContent("53m 04s");

  const bar = within(cells[3]).getByRole("img");
  expect(bar).toHaveAccessibleName(
    "Missing 53m 04s: stale feed 54s, process restart 1m 38s, not running 50m 32s",
  );
  const segments = Array.from(bar.querySelectorAll<HTMLElement>("[data-cause]"));
  expect(segments.map((segment) => segment.dataset.cause)).toEqual([
    "stale_feed",
    "process_restart",
    "not_running",
  ]);
  // The parts reconcile with the whole: 54 + 98 + 3,032 = 3,184 s fills the bar exactly.
  const widths = segments.map((segment) => Number.parseFloat(segment.style.width));
  expect(widths.reduce((sum, width) => sum + width, 0)).toBeCloseTo(100, 6);
  expect(widths[2]).toBeCloseTo((3032 / 3184) * 100, 6);
});

test("2026-10-19 reports its late start instead of 0.00%", () => {
  render(<SessionHistory sessions={sessions()} />);
  const cells = within(desktopRow("2026-10-19")).getAllByRole("cell");

  expect(cells[2]).toHaveTextContent("2.15%");
  expect(cells[2]).not.toHaveTextContent("0.00%");
  expect(cells[3]).toHaveTextContent("8m 17s");
  expect(within(cells[3]).getByRole("img")).toHaveAccessibleName(
    "Missing 8m 17s: not running 8m 16s, awaiting first tick 1s",
  );
});

test("the misleading columns are gone and the span is named for what it is", () => {
  render(<SessionHistory sessions={sessions()} />);
  const table = screen.getByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Missing" })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Process span" })).toBeInTheDocument();
  expect(within(table).queryByRole("columnheader", { name: "Gap loss" })).toBeNull();
  expect(within(table).queryByRole("columnheader", { name: "Ticks/s" })).toBeNull();
  expect(within(table).queryByRole("columnheader", { name: "Uptime" })).toBeNull();
  // The colour key names only causes that occur in the listed sessions.
  expect(screen.getByLabelText("Missing-time cause key")).toHaveTextContent(
    "stale feedprocess restartnot runningawaiting first tick",
  );
});

test("expanding a desktop row shows completeness and the per-cause seconds", async () => {
  const user = userEvent.setup();
  render(<SessionHistory sessions={sessions()} />);
  const toggle = within(screen.getByRole("table")).getByRole("button", { name: "2026-11-05" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  await user.click(toggle);

  expect(toggle).toHaveAttribute("aria-expanded", "true");
  const details = document.getElementById("session-row-2026-11-05") as HTMLElement;
  const field = (label: string) =>
    within(details).getByText(label, { selector: "dt" }).nextElementSibling;
  expect(field("Completeness")).toHaveTextContent("86.22% (19,916 / 23,100s)");
  expect(field("Missing")).toHaveTextContent("53m 04s");
  expect(field("Stale feed")).toHaveTextContent("54s");
  expect(field("Process restart")).toHaveTextContent("1m 38s");
  expect(field("Not running")).toHaveTextContent("50m 32s");
  expect(field("WS connect attempts")).toHaveTextContent("1");
  expect(field("In-process reconnects")).toHaveTextContent("0");
  expect(field("Process span")).toHaveTextContent("06:27:04");
  expect(within(details).queryByText("State")).toBeNull();
  expect(within(details).queryByText("complete")).toBeNull();
});

test("a record written before the breakdown existed falls back instead of inventing zeros", () => {
  const legacy = normalizeSessionSummary({
    trading_date: "2026-07-01",
    data_loss_pct: 0.75,
    frames_written: 10,
    streams: [],
  })!;
  expect(legacy.missing_seconds).toBeUndefined();
  expect(legacy.scheduled_loss_pct).toBeUndefined();

  render(<SessionHistory sessions={[legacy]} />);
  const cells = within(desktopRow("2026-07-01")).getAllByRole("cell");
  expect(cells[2]).toHaveTextContent("0.75%");
  expect(cells[3]).toHaveTextContent("--");
  expect(within(cells[3]).queryByRole("img")).toBeNull();
});

test("the mobile summary leads with schedule loss, missing time and the cause bar", () => {
  render(<SessionHistory sessions={sessions()} />);
  const card = screen.getByRole("button", { name: "2026-11-05 session details" });
  expect(card).toHaveTextContent("13.78% loss");
  expect(card).toHaveTextContent("53m 04s missing");
  expect(card).toHaveTextContent("span 06:27:04");
  expect(card.querySelector("[data-loss-cause-bar]")).toHaveAttribute(
    "aria-label",
    "Missing 53m 04s: stale feed 54s, process restart 1m 38s, not running 50m 32s",
  );
});
