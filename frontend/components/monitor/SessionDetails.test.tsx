import { render, screen, within } from "@testing-library/react";
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

test("extension points: outcome and incidents render only when the backend sends them", () => {
  const plain = render(<SessionDetails session={normalizeSessionSummary(BASE)!} />);
  expect(screen.queryByText("Outcome")).toBeNull();
  expect(screen.queryByLabelText("Incidents")).toBeNull();
  plain.unmount();

  const session = normalizeSessionSummary({
    ...BASE,
    outcome: { label: "degraded", detail: "network outage" },
    incidents: [
      { start_ms: 1_793_818_380_000, end_ms: 1_793_821_440_000, cause: "crash loop", seconds: 3_060 },
      { start_ms: "bad", cause: "dropped" },
      { start_at: 1_793_836_200_000, end_at: 1_793_836_260_000, cause: "restart", seconds: 60 },
    ],
  })!;
  expect(session.incidents).toHaveLength(2);
  render(<SessionDetails session={session} />);
  expect(screen.getByText("Outcome", { selector: "dt" }).nextElementSibling).toHaveTextContent(
    "degraded: network outage",
  );
  const incidents = screen.getByLabelText("Incidents");
  expect(within(incidents).getAllByRole("listitem")).toHaveLength(2);
  expect(incidents).toHaveTextContent("crash loop · 51m 00s");
});

test("outcome may be a bare string; malformed values are ignored", () => {
  expect(normalizeSessionSummary({ ...BASE, outcome: "complete" })!.outcome).toEqual({ label: "complete" });
  expect(normalizeSessionSummary({ ...BASE, outcome: { label: "" } })!.outcome).toBeUndefined();
  expect(normalizeSessionSummary({ ...BASE, incidents: [] })!.incidents).toBeUndefined();
  expect(normalizeSessionSummary({ ...BASE, incidents: "x" })!.incidents).toBeUndefined();
});

test("a legacy record shows dashes for the breakdown and lists recovery only when it failed", () => {
  const view = render(<SessionDetails session={normalizeSessionSummary(BASE)!} />);
  const field = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling;
  expect(field("Completeness")).toHaveTextContent("--");
  expect(field("Missing")).toHaveTextContent("--");
  expect(field("WS connect attempts")).toHaveTextContent("--");
  expect(screen.queryByText("Process restart")).toBeNull();
  expect(screen.queryByText("Recovery")).toBeNull();
  view.unmount();

  render(<SessionDetails session={normalizeSessionSummary({ ...BASE, recovery_abandoned: true })!} />);
  expect(screen.getByText("Recovery", { selector: "dt" }).nextElementSibling).toHaveTextContent("abandoned");
});
