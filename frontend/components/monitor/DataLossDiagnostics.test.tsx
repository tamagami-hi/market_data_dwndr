import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { DataLossDiagnostics } from "@/components/monitor/DataLossDiagnostics";
import { normalizeCaptureStatus } from "@/lib/monitor/viewModel";

test("keeps frozen feed time separate from elapsed missing-frame loss", () => {
  const globals = normalizeCaptureStatus({
    per_underlying: [],
    global: {
      fps: 1,
      frozen_seconds: 188,
      session_loss_pct: 0,
    },
  })!.global;

  render(<DataLossDiagnostics globals={globals} />);

  const frozen = screen.getByText("Frozen seconds").closest(".metric");
  const elapsed = screen.getByText("Elapsed loss").closest(".metric");
  expect(frozen).not.toBeNull();
  expect(elapsed).not.toBeNull();
  expect(within(frozen as HTMLElement).getByText("188")).toBeInTheDocument();
  expect(frozen).not.toHaveTextContent("%");
  expect(within(elapsed as HTMLElement).getByText("0.000%")).toBeInTheDocument();
  expect(elapsed).not.toHaveTextContent("188");
});
