import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { MonitorKpis } from "@/components/monitor/MonitorKpis";
import { normalizeCaptureStatus } from "@/lib/monitor/viewModel";

function globalsFrom(global: Record<string, unknown>) {
  return normalizeCaptureStatus({ per_underlying: [], global: { fps: 1, ...global } })!.global;
}

// 2026-11-06 snapshot: 23,100 grid seconds captured of a 23,100 s session, 8 streams.
const FRIDAY = {
  fps: 0.627,
  captures: 23_100,
  frames_written: 184_800,
  frames_expected: 184_800,
  scheduled_seconds: 23_100,
  scheduled_loss_pct: 0,
};

test("the Captures gauge measures grid seconds against the scheduled session", () => {
  render(<MonitorKpis globals={globalsFrom(FRIDAY)} expectedFrames={23_100} />);
  // Previously divided by frames_expected (all 8 streams): "13% of the session baseline".
  expect(screen.getByTitle("100% of the scheduled session (23,100 s)")).toBeInTheDocument();
});

test("falls back to the per-session frame baseline when the schedule is unknown", () => {
  render(
    <MonitorKpis globals={globalsFrom({ ...FRIDAY, scheduled_seconds: 0, captures: 11_550 })} expectedFrames={23_100} />,
  );
  expect(screen.getByTitle("50% of the scheduled session (23,100 s)")).toBeInTheDocument();
});

test("a retained snapshot's frame rate is labelled and not coloured as a live warning", () => {
  const live = render(<MonitorKpis globals={globalsFrom(FRIDAY)} expectedFrames={23_100} />);
  const liveFps = screen.getByText("0.63");
  expect(liveFps).toHaveClass("text-warning");
  live.unmount();

  render(<MonitorKpis globals={globalsFrom(FRIDAY)} expectedFrames={23_100} isRetained />);
  const fps = screen.getByText("0.63");
  expect(fps).not.toHaveClass("text-warning");
  expect(fps.closest(".metric")).toHaveTextContent("last session, not live");
  expect(screen.getByText("subscribed (last session)")).toBeInTheDocument();
});
