import { expect, test } from "vitest";

import {
  normalizeCalendarDay,
  retainedSessionNotice,
  type CalendarDay,
} from "@/lib/monitor/retainedSession";

const tradingDay: CalendarDay = {
  date: "2026-11-09",
  isTradingDay: true,
  specialSession: null,
  captureWindow: ["09:10", "15:45"],
  tokenReady: true,
};

test("no notice while the page is live", () => {
  expect(
    retainedSessionNotice({ retainedDate: null, tradingDate: "2026-11-09", marketPhase: "OPEN", calendar: null }),
  ).toBeNull();
});

test("Sunday shows Friday's session as the last session, market closed, neutral", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-08",
    marketPhase: "HOLIDAY",
    calendar: null,
  })!;
  expect(notice.title).toBe("Showing the last session (2026-11-06) — today 2026-11-08: market closed");
  expect(notice.severity).toBe("neutral");
  expect(notice.detail).toContain("not live");
});

test("pre-open names the capture start and a missing broker token", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-09",
    marketPhase: "PRE_OPEN",
    calendar: { ...tradingDay, tokenReady: false },
  })!;
  expect(notice.title).toBe(
    "Showing the last session (2026-11-06) — today 2026-11-09: capture starts 09:10, waiting for the broker token",
  );
  expect(notice.severity).toBe("neutral");
});

test("a special session states its hours", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-08",
    marketPhase: "PRE_OPEN",
    calendar: { ...tradingDay, specialSession: { open: "18:00", close: "19:15" }, captureWindow: ["17:55", "19:20"] },
  })!;
  expect(notice.title).toContain("special session 18:00–19:15, capture starts 17:55");
});

test("pre-open without calendar data still says capture has not started", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-09",
    marketPhase: "PRE_OPEN",
    calendar: null,
  })!;
  expect(notice.title).toContain("capture not started yet");
});

test("market open without capture is a danger, not a quiet retained view", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-09",
    marketPhase: "OPEN",
    calendar: tradingDay,
  })!;
  expect(notice.title).toContain("market open but capture is not running");
  expect(notice.severity).toBe("danger");
});

test("after the close, today's own session is neutral but a missed day is a warning", () => {
  const sameDay = retainedSessionNotice({
    retainedDate: "2026-11-09",
    tradingDate: "2026-11-09",
    marketPhase: "CLOSED",
    calendar: tradingDay,
  })!;
  expect(sameDay.title).toBe("Showing today's session (2026-11-09) — session closed");
  expect(sameDay.severity).toBe("neutral");

  const missed = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-09",
    marketPhase: "CLOSED",
    calendar: tradingDay,
  })!;
  expect(missed.title).toContain("no session was captured today");
  expect(missed.severity).toBe("warning");
});

test("a non-trading calendar day reads as closed whatever the phase", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: "2026-11-08",
    marketPhase: null,
    calendar: { ...tradingDay, isTradingDay: false },
  })!;
  expect(notice.title).toContain("market closed");
});

test("an unknown phase still explains that capture is not running", () => {
  const notice = retainedSessionNotice({
    retainedDate: "2026-11-06",
    tradingDate: null,
    marketPhase: null,
    calendar: null,
  })!;
  expect(notice.title).toBe("Showing today's session (2026-11-06) — capture not running");
});

test("normalizes the calendar's today block defensively", () => {
  expect(normalizeCalendarDay(null)).toBeNull();
  expect(normalizeCalendarDay({ date: "2026-11-08" })).toBeNull();
  expect(
    normalizeCalendarDay({
      date: "2026-11-08",
      is_trading_day: true,
      special_session: { open: "18:00", close: "19:15", note: "Muhurat" },
      capture_window: ["17:55", "19:20"],
      token_ready: false,
    }),
  ).toEqual({
    date: "2026-11-08",
    isTradingDay: true,
    specialSession: { open: "18:00", close: "19:15" },
    captureWindow: ["17:55", "19:20"],
    tokenReady: false,
  });
  expect(
    normalizeCalendarDay({ is_trading_day: false, capture_window: ["09:10"], special_session: { open: 1 } }),
  ).toEqual({
    date: null,
    isTradingDay: false,
    specialSession: null,
    captureWindow: null,
    tokenReady: null,
  });
});
