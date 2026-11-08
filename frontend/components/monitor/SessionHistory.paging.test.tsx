import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { SessionHistory } from "@/components/monitor/SessionHistory";
import type { SessionSummary } from "@/lib/api";
import { normalizeSessionSummary } from "@/lib/monitor/sessionSummary";

function row(date: string) {
  return { trading_date: date, data_loss_pct: 0, frames_written: 1, streams: [] };
}

function session(date: string): SessionSummary {
  return normalizeSessionSummary(row(date))!;
}

const PAGES: Record<string, { sessions: unknown[]; total: number; next_before: string | null }> = {
  // First click: everything strictly older than the oldest /api/stats row. The page
  // repeats 2026-10-30 (already shown) to prove rows are deduplicated by date.
  "2026-10-30": {
    sessions: [row("2026-10-30"), row("2026-10-29"), row("2026-10-28")],
    total: 6,
    next_before: "2026-10-28",
  },
  "2026-10-28": { sessions: [row("2026-10-27")], total: 6, next_before: null },
};

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = new URL(String(input), "http://monitor.test");
  const before = url.searchParams.get("before") ?? "";
  const page = PAGES[before];
  return {
    ok: Boolean(page),
    status: page ? 200 : 404,
    statusText: page ? "OK" : "Not Found",
    json: async () => page ?? { detail: "no such page" },
  } as Response;
});

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function dates(): string[] {
  return within(screen.getByRole("table"))
    .getAllByRole("button")
    .map((button) => button.textContent ?? "");
}

test("pages older sessions with before=next_before until the history is exhausted", async () => {
  const user = userEvent.setup();
  render(<SessionHistory sessions={[session("2026-11-06"), session("2026-10-30")]} />);
  expect(screen.getByText("2 recorded sessions")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Load older sessions" }));
  await waitFor(() => expect(dates()).toHaveLength(4));
  const firstUrl = new URL(String(fetchMock.mock.calls[0][0]), "http://monitor.test");
  expect(firstUrl.pathname).toBe("/api/stats/sessions");
  expect(firstUrl.searchParams.get("before")).toBe("2026-10-30");
  expect(firstUrl.searchParams.get("limit")).toBe("100");
  expect(dates()).toEqual(["2026-11-06", "2026-10-30", "2026-10-29", "2026-10-28"]);
  expect(screen.getAllByText("4 of 6 sessions").length).toBeGreaterThan(0);

  await user.click(screen.getByRole("button", { name: "Load older sessions" }));
  await waitFor(() => expect(dates()).toHaveLength(5));
  const secondUrl = new URL(String(fetchMock.mock.calls[1][0]), "http://monitor.test");
  expect(secondUrl.searchParams.get("before")).toBe("2026-10-28");
  expect(dates().at(-1)).toBe("2026-10-27");
  // next_before is null: the button goes away and nothing more is requested.
  expect(screen.queryByRole("button", { name: "Load older sessions" })).toBeNull();
  expect(screen.getByText("5 of 6 sessions")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a failed page is reported and can be retried", async () => {
  const user = userEvent.setup();
  render(<SessionHistory sessions={[session("2026-10-01")]} />);
  await user.click(screen.getByRole("button", { name: "Load older sessions" }));
  expect(await screen.findByText("no such page")).toHaveClass("text-danger");
  expect(screen.getByRole("button", { name: "Load older sessions" })).toBeEnabled();
});
