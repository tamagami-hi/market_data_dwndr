import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { MonitorAlerts } from "@/components/monitor/MonitorAlerts";
import { formatClockTime } from "@/lib/numberFormat";

test("an unreachable backend raises a danger alert with the last update time", () => {
  const lastSuccessAt = new Date(2026, 10, 8, 10, 5, 0).getTime();
  render(
    <MonitorAlerts
      globals={null}
      restError="Stats refresh failed."
      payloadError={null}
      isRestStale
      lastSuccessAt={lastSuccessAt}
    />,
  );
  const alert = screen.getByRole("alert");
  expect(alert).toHaveClass("state-danger");
  expect(alert).toHaveTextContent(`Backend unreachable — last update ${formatClockTime(lastSuccessAt)}`);
  expect(alert).toHaveTextContent("Stats refresh failed.");
  expect(alert).toHaveTextContent("not current");
  // The stale warning is not shown alongside it: one banner, the more specific one.
  expect(screen.queryByText("Historical data is stale")).toBeNull();
});

test("an unreachable backend on first load says nothing has been received", () => {
  render(
    <MonitorAlerts
      globals={null}
      restError="Stats refresh failed."
      payloadError={null}
      isRestStale={false}
      lastSuccessAt={null}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Backend unreachable — no update received yet");
});

test("a single failed poll before the stale threshold is only logged, not bannered", () => {
  render(
    <MonitorAlerts
      globals={null}
      restError="Stats refresh failed."
      payloadError={null}
      isRestStale={false}
      lastSuccessAt={1}
    />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
});
