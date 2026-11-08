import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { DataLossDiagnostics } from "@/components/monitor/DataLossDiagnostics";
import { SessionHistory } from "@/components/monitor/SessionHistory";
import { normalizeCaptureStatus } from "@/lib/monitor/viewModel";
import { normalizeSessionSummary } from "@/lib/monitor/sessionSummary";
import { SESSION_2026_11_05_DESCRIBED } from "@/test/fixtures/session-2026-11-05";

const INDICES = "BANKEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTY, SENSEX";

function nov5() {
  return normalizeSessionSummary(SESSION_2026_11_05_DESCRIBED)!;
}

async function openDetails() {
  const user = userEvent.setup();
  render(<SessionHistory sessions={[nov5()]} />);
  await user.click(within(screen.getByRole("table")).getByRole("button", { name: "2026-11-05" }));
  return document.getElementById("session-row-2026-11-05") as HTMLElement;
}

test("the normalizer keeps the backend's outcome, causes and incident timeline", () => {
  const session = nov5();
  expect(session.outcome).toBe("partial");
  expect(session.outcome_basis).toBe("archive");
  expect(session.completeness_pct).toBe(86.216);
  expect(session.incidents_recorded).toBe(true);
  expect(session.process_starts).toBe(104);
  expect(session.failed_starts).toBe(100);
  expect(session.escalations).toBe(2);
  expect(session.loss_causes?.restart_loop_seconds).toBe(3_070);
  const loop = session.outages?.find((outage) => outage.cause === "restart_loop");
  expect(loop?.failed_starts).toBe(100);
  expect(loop?.skipped_indices).toHaveLength(6);
  expect(session.datasets_missing).toHaveLength(6);
});

test("the history row carries the outcome chip, completeness and the ledger's causes", () => {
  render(<SessionHistory sessions={[nov5()]} />);
  const button = within(screen.getByRole("table")).getByRole("button", { name: "2026-11-05" });
  const row = button.closest("tr") as HTMLElement;
  const cells = within(row).getAllByRole("cell");

  expect(within(cells[0]).getByText("partial")).toHaveClass("status-danger");
  expect(cells[2]).toHaveTextContent("86.22%");
  expect(cells[3]).toHaveTextContent("53m 04s");
  // The 51 minutes are a restart loop, not "not running".
  expect(within(cells[3]).getByRole("img")).toHaveAccessibleName(
    "Missing 53m 04s: stale feed 54s, restart loop 51m 10s, process restart 45s, process down 15s",
  );
});

test("details lead with the plain-language explanation", async () => {
  const details = await openDetails();
  const lines = within(within(details).getByLabelText("What happened")).getAllByRole("listitem");
  expect(lines[0]).toHaveTextContent(
    "partial: 86.2% of 23100 scheduled seconds captured (archive figure is worse and decides)",
  );
  expect(lines[1]).toHaveTextContent(
    "51 min lost to a restart loop (capture repeatedly failed to start or connect)",
  );
  expect(details).toHaveTextContent(
    `restart loop 10:23-11:14: 100 failed start(s) (TickerConnectFailed) after CaptureStalledError; index chains skipped: ${INDICES}`,
  );
});

test("the outage timeline shows the 10:23 → 11:14 restart loop in IST", async () => {
  const details = await openDetails();
  const outages = within(within(details).getByLabelText("Outages")).getAllByRole("listitem");
  expect(outages).toHaveLength(2);
  expect(outages[0]).toHaveTextContent("10:23:00 → 11:14:10");
  expect(outages[0]).toHaveTextContent("51m 10s");
  expect(outages[0]).toHaveTextContent("restart loop");
  expect(outages[0]).toHaveTextContent("after CaptureStalledError · 100 failed starts (TickerConnectFailed)");
  expect(outages[0]).toHaveTextContent(`index chains skipped: ${INDICES}`);
  expect(outages[1]).toHaveTextContent("14:10:00 → 14:10:45");
  expect(outages[1]).toHaveTextContent("process restart");
});

test("details give honest counts, missing datasets, stops, EOD and both completeness figures", async () => {
  const details = await openDetails();
  const counts = within(details).getByLabelText("Incident counts");
  const count = (label: string) => within(counts).getByText(label).nextElementSibling;
  expect(count("Process starts")).toHaveTextContent("104");
  expect(count("Failed starts")).toHaveTextContent("100");
  expect(count("Process exits")).toHaveTextContent("102");
  expect(count("Escalations")).toHaveTextContent("2");

  const datasets = within(details).getByLabelText("Datasets missing");
  expect(within(datasets).getAllByRole("listitem")).toHaveLength(6);
  expect(datasets).toHaveTextContent("NIFTY50m 30s10:23:40 → 11:14:10100 starts · spot must be > 0");
  // One shared quote error, listed once rather than six times.
  expect(within(datasets).getAllByText(/^quote error:/)).toHaveLength(1);

  const stops = within(details).getByLabelText("Stops and end of day");
  expect(stops).toHaveTextContent("15:40:00stopped: market close · clean");
  expect(stops).toHaveTextContent("15:42:00EOD: ok · 8 files · archive loss 13.79% (worst NIFTY of 8)");

  const completeness = within(details).getByLabelText("Completeness");
  expect(completeness).toHaveTextContent("Telemetry completeness86.22% (19,916 / 23,100s)");
  expect(completeness).toHaveTextContent("Archive completeness86.21% (worst NIFTY, 3 gaps, 8 artifacts)");
  expect(details).toHaveTextContent("The archive figure is worse and decides the outcome.");
});

test("the data-loss panel heads with the day's outcome and explanation", () => {
  const globals = normalizeCaptureStatus({ per_underlying: [], global: { fps: 1 } })!.global;
  render(<DataLossDiagnostics globals={globals} retainedDate="2026-11-05" sessionOutcome={nov5()} />);
  const strip = screen.getByLabelText("Session outcome");
  expect(within(strip).getByText("partial")).toHaveClass("status-danger");
  expect(strip).toHaveTextContent("86.22% captured");
  const why = within(within(strip).getByLabelText("Why")).getAllByRole("listitem");
  expect(why[0]).toHaveTextContent("partial: 86.2% of 23100 scheduled seconds captured");
  expect(why).toHaveLength(5);
  expect(why[4]).toHaveTextContent("+8 more in Session history");
});
