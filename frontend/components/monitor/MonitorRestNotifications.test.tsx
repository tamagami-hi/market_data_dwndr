import { render } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const reportMonitorRestError = vi.fn();

vi.mock("@/components/operator-events/OperatorEventsProvider", () => ({
  useOperatorEvents: () => ({ reportMonitorRestError }),
}));

import { MonitorRestNotifications } from "@/components/monitor/MonitorRestNotifications";

beforeEach(() => {
  reportMonitorRestError.mockReset();
});

test("does not report a false recovery before the first successful refresh", () => {
  const view = render(
    <MonitorRestNotifications error="Session history request timed out." hasCompletedRefresh={false} />,
  );
  expect(reportMonitorRestError).toHaveBeenLastCalledWith(
    "Session history request timed out.",
  );

  reportMonitorRestError.mockClear();
  view.rerender(
    <MonitorRestNotifications error={null} hasCompletedRefresh={false} />,
  );
  expect(reportMonitorRestError).not.toHaveBeenCalled();

  view.rerender(
    <MonitorRestNotifications error={null} hasCompletedRefresh />,
  );
  expect(reportMonitorRestError).toHaveBeenCalledWith(null);
});
