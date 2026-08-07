import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { DataLossDiagnostics } from "@/components/monitor/DataLossDiagnostics";
import { normalizeCaptureStatus } from "@/lib/monitor/viewModel";

function globalsFrom(global: Record<string, unknown>) {
  return normalizeCaptureStatus({ per_underlying: [], global: { fps: 1, ...global } })!.global;
}

test("separates gap-only loss from total loss including stale seconds", () => {
  // 188 of 1,000 elapsed seconds were stale and therefore never written; the write path
  // itself lost nothing. Both facts have to be readable at a glance, because they call
  // for different responses: a gap is a pipeline bug, stale time is an upstream outage.
  const globals = globalsFrom({
    stale_seconds: 188,
    stale_events: 2,
    grid_seconds_elapsed: 1_000,
    session_loss_pct: 0,
    data_loss_pct: 18.8,
  });

  render(<DataLossDiagnostics globals={globals} />);

  const stale = screen.getByText("Stale seconds").closest(".metric");
  const elapsed = screen.getByText("Elapsed loss").closest(".metric");
  const total = screen.getByText("Data loss").closest(".metric");
  expect(within(stale as HTMLElement).getByText("188")).toBeInTheDocument();
  expect(stale).toHaveTextContent("18.8% of session not written");
  expect(within(elapsed as HTMLElement).getByText("0.000%")).toBeInTheDocument();
  expect(within(total as HTMLElement).getByText("18.800%")).toBeInTheDocument();
  expect(within(screen.getByText("Stale events").closest(".metric") as HTMLElement)
    .getByText("2")).toBeInTheDocument();
});

test("drops the writer-lag and disk-runway tiles", () => {
  render(<DataLossDiagnostics globals={globalsFrom({ writer_lag_max: 7 })} />);
  expect(screen.queryByText("Writer lag")).toBeNull();
  expect(screen.queryByText("Disk runway")).toBeNull();
});

test("a clean session reports no stale data written", () => {
  render(<DataLossDiagnostics globals={globalsFrom({ grid_seconds_elapsed: 500 })} />);
  const stale = screen.getByText("Stale seconds").closest(".metric");
  expect(stale).toHaveTextContent("no stale data written");
});



// --- live stale spell banner --------------------------------------------------
//
// The 2026-08-04/05/06 sessions lost 9, 72, and 91 minutes of market data to a feed that
// had stopped delivering ticks, and none of it was visible until the end-of-day session
// history. A spell in progress now states itself.

test("badges an armed stale spell as a fault the process will restart over", () => {
  render(
    <DataLossDiagnostics
      globals={globalsFrom({ stale_spell_seconds: 47, recovery_armed: true, stale: true })}
    />,
  );
  const banner = screen.getByRole("status");
  expect(banner).toHaveTextContent("Feed stale for 47s");
  expect(banner).toHaveTextContent("will restart itself");
  expect(banner.className).toContain("text-danger");
});

test("pre-open silence reads as normal, not as a fault", () => {
  render(
    <DataLossDiagnostics
      globals={globalsFrom({ stale_spell_seconds: 120, recovery_armed: false, stale: true })}
    />,
  );
  const banner = screen.getByRole("status");
  expect(banner).toHaveTextContent("No ticks for 120s");
  expect(banner).toHaveTextContent("not trading yet");
  expect(banner.className).not.toContain("text-danger");
});

test("an abandoned recovery says capture is up but receiving nothing", () => {
  render(
    <DataLossDiagnostics
      globals={globalsFrom({
        stale_spell_seconds: 900,
        recovery_armed: true,
        recovery_abandoned: true,
        escalations: 3,
        stale: true,
      })}
    />,
  );
  const banner = screen.getByRole("status");
  expect(banner).toHaveTextContent("Recovery abandoned after 3 restarts");
  expect(banner).toHaveTextContent("receiving no data");
});

test("a healthy feed shows no banner at all", () => {
  render(<DataLossDiagnostics globals={globalsFrom({ grid_seconds_elapsed: 500 })} />);
  expect(screen.queryByRole("status")).toBeNull();
});



// --- feed health vs market phase ----------------------------------------------


test("market phase and feed health are shown as separate dimensions", () => {
  // PRE_OPEN + HEALTHY is a perfectly normal state; conflating the two is what made a
  // routine pre-open look like a dead feed.
  render(
    <DataLossDiagnostics
      globals={globalsFrom({ market_phase: "PRE_OPEN", feed_health: "HEALTHY" })}
    />,
  );
  const strip = screen.getByLabelText("Feed health");
  expect(strip).toHaveTextContent("PRE_OPEN");
  expect(strip).toHaveTextContent("feed healthy");
});

test("a dead transport reads differently from a quiet market", () => {
  const dead = render(
    <DataLossDiagnostics
      globals={globalsFrom({ market_phase: "OPEN", feed_health: "TRANSPORT_STALE" })}
    />,
  );
  expect(screen.getByLabelText("Feed health")).toHaveTextContent("feed dead");
  dead.unmount();

  render(
    <DataLossDiagnostics
      globals={globalsFrom({ market_phase: "OPEN", feed_health: "QUIET" })}
    />,
  );
  const quiet = screen.getByLabelText("Feed health");
  expect(quiet).toHaveTextContent("market quiet");
  expect(quiet.className).not.toContain("text-danger");
});

test("artifact-level staleness names the datasets that stopped updating", () => {
  render(
    <DataLossDiagnostics
      globals={globalsFrom({
        market_phase: "OPEN",
        feed_health: "ARTIFACT_STALE",
        stale_artifacts: ["INDICES_FnO", "NIFTY"],
      })}
    />,
  );
  const strip = screen.getByLabelText("Feed health");
  expect(strip).toHaveTextContent("dataset stale");
  expect(strip).toHaveTextContent("INDICES_FnO, NIFTY");
});

// --- scheduled loss breakdown -------------------------------------------------


test("downtime is shown as a distinct cause of missing data", () => {
  render(
    <DataLossDiagnostics
      globals={globalsFrom({
        scheduled_seconds_elapsed: 3_600,
        captured_seconds: 3_000,
        missing_seconds: 600,
        scheduled_loss_pct: 16.667,
        stale_feed_seconds: 100,
        downtime_seconds: 500,
      })}
    />,
  );
  const breakdown = screen.getByLabelText("Loss breakdown");
  expect(breakdown).toHaveTextContent("Scheduled");
  expect(breakdown).toHaveTextContent("3,600s");
  expect(breakdown).toHaveTextContent("Captured");
  expect(breakdown).toHaveTextContent("3,000s");
  expect(breakdown).toHaveTextContent("Missing");
  expect(breakdown).toHaveTextContent("600s");
  expect(breakdown).toHaveTextContent("feed stale");
  expect(breakdown).toHaveTextContent("100s");
  expect(breakdown).toHaveTextContent("downtime");
  expect(breakdown).toHaveTextContent("500s");
});

test("a complete session shows no cause list at all", () => {
  render(
    <DataLossDiagnostics
      globals={globalsFrom({
        scheduled_seconds_elapsed: 1_000,
        captured_seconds: 1_000,
        missing_seconds: 0,
      })}
    />,
  );
  const breakdown = screen.getByLabelText("Loss breakdown");
  expect(breakdown).toHaveTextContent("Missing");
  expect(breakdown).not.toHaveTextContent("downtime");
});

test("before the session starts there is no scheduled denominator to report", () => {
  render(<DataLossDiagnostics globals={globalsFrom({ scheduled_seconds_elapsed: 0 })} />);
  expect(screen.queryByLabelText("Loss breakdown")).toBeNull();
});
