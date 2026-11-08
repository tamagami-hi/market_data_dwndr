import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { SessionDetails } from "@/components/monitor/SessionDetails";
import { normalizeSessionSummary } from "@/lib/monitor/sessionSummary";

const BASE = {
  trading_date: "2026-11-05",
  frames_written: 159_328,
  frames_expected: 184_800,
  data_loss_pct: 13.784,
  streams: [],
};

const field = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling;

test("a row the backend did not describe shows no outcome, explanation or ledger section", () => {
  render(<SessionDetails session={normalizeSessionSummary(BASE)!} />);
  expect(screen.queryByLabelText("What happened")).toBeNull();
  expect(screen.queryByLabelText("Incident counts")).toBeNull();
  expect(screen.queryByText(/No incident ledger/)).toBeNull();
  expect(field("Telemetry completeness")).toHaveTextContent("--");
  expect(field("Archive completeness")).toHaveTextContent("not reconciled");
  expect(field("Missing")).toHaveTextContent("--");
  expect(field("WS connect attempts")).toHaveTextContent("--");
  expect(screen.queryByText("Process restart")).toBeNull();
  expect(screen.queryByText("Recovery")).toBeNull();
});

test("a day recorded before the ledger says so instead of showing zero counts", () => {
  render(
    <SessionDetails
      session={normalizeSessionSummary({
        ...BASE,
        trading_date: "2026-10-19",
        incidents_recorded: false,
        process_starts: null,
        failed_starts: null,
        escalations: 0,
        explanation: ["partial: 97.8% of 23100 scheduled seconds captured"],
      })!}
    />,
  );
  expect(screen.getByText("No incident ledger for this date (recorded before 2026-11-08)."))
    .toBeInTheDocument();
  expect(screen.queryByLabelText("Incident counts")).toBeNull();
  expect(screen.queryByText("Escalations")).toBeNull();
  expect(screen.getByLabelText("What happened")).toHaveTextContent("partial: 97.8%");
});

test("lists recovery only when it failed, and names a special session", () => {
  render(
    <SessionDetails
      session={normalizeSessionSummary({
        ...BASE,
        recovery_abandoned: true,
        session_window: {
          open: "18:00",
          close: "19:15",
          is_special: true,
          note: "Muhurat",
          trading_day: true,
          scheduled_seconds: 4_500,
          windows: [[1, 2]],
        },
      })!}
    />,
  );
  expect(field("Recovery")).toHaveTextContent("abandoned");
  expect(screen.getByText("special session 18:00–19:15 · Muhurat")).toBeInTheDocument();
});
