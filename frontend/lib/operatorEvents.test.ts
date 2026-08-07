import { beforeEach, describe, expect, test } from "vitest";

import {
  clearExpiredDailyEvents,
  deriveCaptureEvents,
  indiaDayKey,
  loadDailyEvents,
  mergeDailyEvents,
  saveDailyEvents,
  type OperationalEvent,
} from "@/lib/operatorEvents";
import type { GlobalStatus } from "@/lib/wsTypes";

function makeEvent(index: number, dayKey = "2026-07-29"): OperationalEvent {
  return {
    id: `event-${index}`,
    ts: Date.parse(`${dayKey}T09:15:00+05:30`) + index,
    dayKey,
    source: "session",
    severity: "info",
    title: `Message ${index}`,
    detail: "",
    isLog: true,
    isNotification: false,
    isRead: true,
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("daily operational event storage", () => {
  test("uses the India-market calendar day at UTC boundaries", () => {
    expect(indiaDayKey(Date.parse("2026-07-29T18:29:59.999Z"))).toBe("2026-07-29");
    expect(indiaDayKey(Date.parse("2026-07-29T18:30:00.000Z"))).toBe("2026-07-30");
  });

  test("retains every current-day event without the old 300-message cap", () => {
    const events = Array.from({ length: 1_001 }, (_, index) => makeEvent(index));
    expect(mergeDailyEvents([], events, "2026-07-29")).toHaveLength(1_001);
  });

  test("restores current-day events newest first and starts empty after rollover", () => {
    saveDailyEvents(window.localStorage, "2026-07-29", [makeEvent(1), makeEvent(2)]);
    expect(loadDailyEvents(window.localStorage, "2026-07-29").events.map(({ id }) => id))
      .toEqual(["event-2", "event-1"]);
    expect(loadDailyEvents(window.localStorage, "2026-07-30").events).toEqual([]);
  });

  test("deduplicates stable ids while preserving repeated messages with different ids", () => {
    const first = makeEvent(1);
    const repeatedText = { ...first, id: "event-2", ts: first.ts + 1 };
    expect(mergeDailyEvents([first], [first, repeatedText], "2026-07-29"))
      .toEqual([repeatedText, first]);
  });

  test("keeps read state monotonic when cross-tab snapshots are merged", () => {
    const unread = { ...makeEvent(1), isNotification: true, isRead: false };
    const read = { ...unread, isRead: true };

    expect(mergeDailyEvents([read], [unread], "2026-07-29"))
      .toEqual([read]);
    expect(mergeDailyEvents([unread], [read], "2026-07-29"))
      .toEqual([read]);
  });

  test("degrades safely when persisted data is malformed", () => {
    window.localStorage.setItem(
      "tickvault:operational-events:v1:2026-07-29",
      "{broken",
    );
    const result = loadDailyEvents(window.localStorage, "2026-07-29");
    expect(result.events).toEqual([]);
    expect(result.error).toMatch(/could not be restored/i);
  });

  test("removes every expired TickVault day while preserving current and unrelated keys", () => {
    window.localStorage.setItem(
      "tickvault:operational-events:v1:2026-07-27",
      "old",
    );
    window.localStorage.setItem(
      "tickvault:operational-events:v1:2026-07-28",
      "old",
    );
    window.localStorage.setItem(
      "tickvault:operational-events:v1:2026-07-29",
      "current",
    );
    window.localStorage.setItem("another-app:key", "keep");

    expect(clearExpiredDailyEvents(window.localStorage, "2026-07-29")).toBeNull();
    expect(window.localStorage.getItem(
      "tickvault:operational-events:v1:2026-07-27",
    )).toBeNull();
    expect(window.localStorage.getItem(
      "tickvault:operational-events:v1:2026-07-28",
    )).toBeNull();
    expect(window.localStorage.getItem(
      "tickvault:operational-events:v1:2026-07-29",
    )).toBe("current");
    expect(window.localStorage.getItem("another-app:key")).toBe("keep");
  });

  test("rejects persisted timestamps that are outside their declared market day", () => {
    const wrongDay = {
      version: 1,
      dayKey: "2026-07-29",
      events: [{ ...makeEvent(1), ts: Date.parse("2026-07-30T00:00:00+05:30") }],
    };
    window.localStorage.setItem(
      "tickvault:operational-events:v1:2026-07-29",
      JSON.stringify(wrongDay),
    );
    expect(loadDailyEvents(window.localStorage, "2026-07-29").events).toEqual([]);
  });
});

describe("capture notification transitions", () => {
  const snapshot = (values: Partial<GlobalStatus>) => ({
    stale: false,
    degraded: false,
    ingestion_degraded: false,
    reconnects: 0,
    exhausted: false,
    data_age_ms: 0,
    ...values,
  }) as GlobalStatus;

  test("emits stale once, then recovery once", () => {
    const fresh = snapshot({});
    const stale = snapshot({ stale: true, degraded: true, data_age_ms: 4_000 });
    const recovered = snapshot({});
    expect(deriveCaptureEvents(fresh, stale).map(({ title }) => title))
      .toEqual(["Live feed is stale"]);
    expect(deriveCaptureEvents(stale, stale)).toEqual([]);
    expect(deriveCaptureEvents(stale, recovered).map(({ title }) => title))
      .toEqual(["Live feed recovered"]);
  });

  test("emits reconnect and abandonment only when their state advances", () => {
    const previous = snapshot({ reconnects: 1 });
    const next = snapshot({ reconnects: 2, exhausted: true });
    const titles = deriveCaptureEvents(previous, next).map(({ title }) => title);
    expect(titles).toEqual(["Ticker reconnected", "Recovery abandoned"]);
    expect(deriveCaptureEvents(next, next)).toEqual([]);
  });

  test("announces each restart escalation over a dead feed", () => {
    const previous = snapshot({ escalations: 0 });
    const next = snapshot({
      stale: true,
      degraded: true,
      escalations: 1,
      stale_spell_seconds: 62,
    });
    const events = deriveCaptureEvents(previous, next);
    const escalation = events.find(
      ({ title }) => title === "Capture restarting over a dead feed",
    );
    expect(escalation).toBeDefined();
    expect(escalation?.detail).toContain("62s");
    expect(escalation?.severity).toBe("danger");
  });

  test("emits recovery transitions for exhausted and degraded ingestion states", () => {
    const impaired = snapshot({
      exhausted: true,
      ingestion_degraded: true,
    });
    const recovered = snapshot({});

    expect(deriveCaptureEvents(impaired, recovered).map(({ title }) => title))
      .toEqual(["Feed recovery resumed", "Capture ingestion recovered"]);
  });
});
