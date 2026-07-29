import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { SessionLogs } from "@/components/monitor/SessionLogs";
import type { OperationalEvent } from "@/lib/operatorEvents";

function event(
  id: string,
  severity: OperationalEvent["severity"],
  detail = "",
): OperationalEvent {
  return {
    id,
    ts: Date.parse("2026-07-29T10:00:00+05:30"),
    dayKey: "2026-07-29",
    source: "capture",
    severity,
    title: `${severity} event`,
    detail,
    isLog: true,
    isNotification: true,
    isRead: false,
  };
}

test("renders every daily severity and optional detail", () => {
  const { container } = render(
    <SessionLogs
      logs={[
        event("1", "danger", "operator action required"),
        event("2", "warning"),
        event("3", "success"),
        event("4", "info"),
      ]}
    />,
  );

  expect(screen.getByText("4 observed today")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open log viewer" }));
  expect(screen.getAllByText("operator action required")).toHaveLength(2);
  expect(container.querySelectorAll(".text-danger")).toHaveLength(2);
  expect(container.querySelectorAll(".text-warning")).toHaveLength(2);
  expect(container.querySelectorAll(".text-success")).toHaveLength(2);
  expect(container.querySelectorAll(".text-secondary").length).toBeGreaterThanOrEqual(2);
});
