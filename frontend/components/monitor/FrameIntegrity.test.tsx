import { render } from "@testing-library/react";
import { expect, test } from "vitest";

import { FrameIntegrity } from "@/components/monitor/FrameIntegrity";
import { progressColor } from "@/lib/monitor/progressColor";
import type { GlobalStatus, PerUnderlyingStatus } from "@/lib/wsTypes";

test("uses the continuous progress color for the gauge and each stream bar", () => {
  const globals = { frame_loss_pct: 50 } as GlobalStatus;
  const rows = [{
    underlying: "NIFTY",
    frames_written: 75,
    frame_loss_pct: 25,
  }] as PerUnderlyingStatus[];

  const { container } = render(
    <FrameIntegrity rows={rows} globals={globals} expectedFrames={100} />,
  );

  const gauge = container.querySelector("[data-progress-gauge]");
  const bar = container.querySelector("[data-progress-bar]");
  expect(gauge).toHaveAttribute("stroke", progressColor(50));
  expect(bar).toHaveStyle({ backgroundColor: progressColor(75) });
  expect(gauge?.getAttribute("stroke")).not.toMatch(/--danger|--warning|--success/);
  expect(bar?.getAttribute("style")).not.toMatch(/--danger|--warning|--success/);
});

test("calls the shortfall lost, not remaining, once the session has closed", () => {
  const rows = [{ underlying: "NIFTY", frames_written: 19_916, frame_loss_pct: 13.784 }] as PerUnderlyingStatus[];
  const live = render(
    <FrameIntegrity rows={rows} globals={{ frame_loss_pct: 13.784, market_phase: "OPEN" } as GlobalStatus} expectedFrames={23_100} />,
  );
  expect(live.container).toHaveTextContent("19,916 · remaining 13.8%");
  live.unmount();

  const closed = render(
    <FrameIntegrity rows={rows} globals={{ frame_loss_pct: 13.784, market_phase: "CLOSED" } as GlobalStatus} expectedFrames={23_100} />,
  );
  expect(closed.container).toHaveTextContent("19,916 · lost 13.8%");
  closed.unmount();

  const retained = render(
    <FrameIntegrity
      rows={rows}
      globals={{ frame_loss_pct: 13.784 } as GlobalStatus}
      expectedFrames={23_100}
      retainedDate="2026-11-05"
    />,
  );
  expect(retained.container).toHaveTextContent("lost 13.8%");
  expect(retained.container).toHaveTextContent("baseline 23,100 frames / session · last session 2026-11-05");
});
