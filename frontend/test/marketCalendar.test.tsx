import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import MarketCalendarPage from "@/app/calendar/page";
import { minusMinutes, OPERATOR_TOKEN_HEADER, saveSpecialSession } from "@/lib/marketCalendar";

const CALENDAR = {
  holidays: [{ date: "2026-11-24", note: "Guru Nanak Jayanti" }],
  special_sessions: [
    {
      date: "2026-11-08",
      open: "14:20",
      close: "15:30",
      token_fetch_at: "13:00",
      capture_lead_minutes: 5,
      note: "Diwali muhurat",
    },
  ],
  env_holidays: ["2026-09-05"],
  regular_hours: {
    market_open: "09:15",
    market_close: "15:40",
    capture_window: ["09:10", "15:40"],
    token_window: ["08:31", "09:09"],
  },
  today: {
    date: "2026-11-08",
    local_time: "10:00:00",
    is_trading_day: true,
    market_phase: "PRE_OPEN",
    special_session: {
      date: "2026-11-08",
      open: "14:20",
      close: "15:30",
      token_fetch_at: "13:00",
      capture_lead_minutes: 5,
      note: "Diwali muhurat",
    },
    token_window: ["13:00", "14:15"],
    capture_window: ["14:15", "15:30"],
    token_ready: false,
    token_fetched_at: null,
    capture_running: false,
    automation: null,
  },
};

vi.mock("next/navigation", () => ({ usePathname: () => "/calendar" }));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => jsonResponse(CALENDAR));
  vi.stubGlobal("fetch", fetchMock);
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("minusMinutes shifts HH:MM and clamps at midnight", () => {
  expect(minusMinutes("14:20", 5)).toBe("14:15");
  expect(minusMinutes("00:10", 30)).toBe("00:00");
  expect(minusMinutes("bad", 5)).toBe("");
});

test("saveSpecialSession sends the operator key and omits a blank token time", async () => {
  await saveSpecialSession("key-123", {
    date: "2026-11-08",
    open: "14:20",
    close: "15:30",
    token_fetch_at: "",
    capture_lead_minutes: 5,
    note: "",
  });
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toMatch(/\/api\/calendar\/special-sessions$/);
  expect(init.method).toBe("PUT");
  expect((init.headers as Record<string, string>)[OPERATOR_TOKEN_HEADER]).toBe("key-123");
  expect(JSON.parse(String(init.body))).not.toHaveProperty("token_fetch_at");
});

test("calendar page shows today's special session, lists, and the token action", async () => {
  render(<MarketCalendarPage />);
  expect(await screen.findByText("14:20–15:30")).toBeInTheDocument();
  expect(screen.getByText("13:00–14:15")).toBeInTheDocument();
  expect(screen.getByText(/Guru Nanak Jayanti/)).toBeInTheDocument();
  expect(screen.getByText("2026-09-05")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Fetch token now" })).toBeEnabled();
});

test("changes without an operator key are refused before any request", async () => {
  const user = userEvent.setup();
  render(<MarketCalendarPage />);
  await user.click(await screen.findByRole("button", { name: "Fetch token now" }));
  expect(await screen.findByText("Enter the operator key first.")).toBeInTheDocument();
  expect(fetchMock.mock.calls.every(([, init]) => !init || !(init as RequestInit).method)).toBe(true);
});

test("a rejected operator key is reported", async () => {
  const user = userEvent.setup();
  render(<MarketCalendarPage />);
  await screen.findByText("14:20–15:30");
  fetchMock.mockImplementation(async (_url: string, init?: RequestInit) =>
    init?.method ? jsonResponse({ detail: "invalid operator credential" }, 401) : jsonResponse(CALENDAR),
  );
  await user.type(screen.getByPlaceholderText(/Required to save changes/), "wrong");
  await user.click(screen.getByRole("button", { name: "Fetch token now" }));
  await waitFor(() => expect(screen.getByText("Operator key was rejected.")).toBeInTheDocument());
});

test("the special-day form starts from the configured regular hours, not a fixed occasion", async () => {
  render(<MarketCalendarPage />);
  await screen.findByText("14:20–15:30");
  expect(screen.getByLabelText("Market open (IST)")).toHaveValue("09:15");
  expect(screen.getByLabelText("Market close (IST)")).toHaveValue("15:40");
  expect(screen.getByPlaceholderText("e.g. Special trading session")).toBeInTheDocument();
  expect(screen.queryByPlaceholderText(/diwali/i)).not.toBeInTheDocument();
});

test("the operator key is cleared after each use and never stored", async () => {
  window.sessionStorage.setItem("tickvault.operatorToken", "left-by-an-older-release");
  const user = userEvent.setup();
  render(<MarketCalendarPage />);
  await screen.findByText("14:20–15:30");
  expect(window.sessionStorage.getItem("tickvault.operatorToken")).toBeNull();

  const keyInput = screen.getByPlaceholderText(/Required to save changes/) as HTMLInputElement;
  expect(keyInput.value).toBe("");

  // A successful change uses the key once, then clears it.
  await user.type(keyInput, "the-key");
  await user.click(screen.getByRole("button", { name: "Save special open day" }));
  await screen.findByText(/Special session saved/);
  expect(keyInput.value).toBe("");
  expect(window.sessionStorage.length).toBe(0);
  const saved = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
  expect((saved![1] as RequestInit).headers).toMatchObject({ "X-Release-Maintenance-Token": "the-key" });

  // The next change needs the key entered again.
  await user.click(screen.getByRole("button", { name: "Fetch token now" }));
  expect(await screen.findByText("Enter the operator key first.")).toBeInTheDocument();

  // A rejected key is cleared too.
  fetchMock.mockImplementation(async (_url: string, init?: RequestInit) =>
    init?.method ? jsonResponse({ detail: "invalid operator credential" }, 401) : jsonResponse(CALENDAR),
  );
  await user.type(keyInput, "wrong");
  await user.click(screen.getByRole("button", { name: "Fetch token now" }));
  await screen.findByText("Operator key was rejected.");
  expect(keyInput.value).toBe("");
});
