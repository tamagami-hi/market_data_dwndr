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
