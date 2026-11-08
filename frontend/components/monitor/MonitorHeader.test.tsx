import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("@/components/monitor/MonitorConnections", () => ({
  MonitorConnections: () => null,
}));

import { MonitorHeader, RetainedSessionBanner } from "@/components/monitor/MonitorHeader";
import { retainedSessionNotice } from "@/lib/monitor/retainedSession";
import { normalizeCaptureStatus } from "@/lib/monitor/viewModel";

const globals = normalizeCaptureStatus({
  per_underlying: [],
  global: { fps: 0.627, uptime_ms: 23_395_629, stale: false, degraded: false },
})!.global;

const sundayNotice = retainedSessionNotice({
  retainedDate: "2026-11-06",
  tradingDate: "2026-11-08",
  marketPhase: "HOLIDAY",
  calendar: null,
});

test("Sunday: the header says retained in a neutral tone and names today's phase", () => {
  render(
    <MonitorHeader
      globals={globals}
      tradingDate="2026-11-08"
      marketPhase="HOLIDAY"
      captureRunning={false}
      notice={sundayNotice}
      refreshWindow={null}
      lastSuccessAt={null}
    />,
  );
  const context = screen.getByLabelText("Monitor session context");
  expect(context).toHaveTextContent("2026-11-08 · HOLIDAY / last session span 06:29:55");
  const status = context.querySelector(".status-indicator");
  expect(status).toHaveTextContent("Retained");
  expect(status).toHaveClass("status-neutral");
  expect(status).not.toHaveClass("status-success");
});

test("Sunday: a visible banner explains the page is showing Friday", () => {
  render(<RetainedSessionBanner notice={sundayNotice} />);
  const banner = screen.getByRole("status");
  expect(banner).toHaveTextContent(
    "Showing the last session (2026-11-06) — today 2026-11-08: market closed",
  );
  expect(banner).toHaveClass("state-neutral");
});

test("market open without capture turns the header and banner red", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-09",
    marketPhase: "OPEN",
    calendar: null,
  });
  render(
    <>
      <MonitorHeader
        globals={globals}
        tradingDate="2026-11-09"
        marketPhase="OPEN"
        captureRunning={false}
        notice={notice}
        refreshWindow={null}
        lastSuccessAt={null}
      />
      <RetainedSessionBanner notice={notice} />
    </>,
  );
  expect(screen.getByText("Not capturing")).toHaveClass("status-danger");
  expect(screen.getByRole("alert")).toHaveTextContent("market open but capture is not running");
});

test("live capture keeps the live state and shows no banner", () => {
  const view = render(
    <MonitorHeader
      globals={globals}
      tradingDate="2026-11-09"
      marketPhase="OPEN"
      captureRunning
      refreshWindow={null}
      lastSuccessAt={null}
    />,
  );
  expect(screen.getByText("Capturing")).toHaveClass("status-success");
  expect(screen.getByLabelText("Monitor session context")).toHaveTextContent("uptime 06:29:55");
  view.unmount();
  const banner = render(<RetainedSessionBanner notice={null} />);
  expect(banner.container).toBeEmptyDOMElement();
});
