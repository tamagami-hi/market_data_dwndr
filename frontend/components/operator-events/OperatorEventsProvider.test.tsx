import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const topicMocks = vi.hoisted(() => ({
  handlers: new Map<string, (envelope: { type: string; payload?: unknown }) => void>(),
  registrations: [] as string[],
}));

vi.mock("@/lib/useTopic", () => ({
  useTopicEnvelopes: (
    connection: { id: string },
    handler: (envelope: { type: string; payload?: unknown }) => void,
  ) => {
    topicMocks.registrations.push(connection.id);
    topicMocks.handlers.set(connection.id, handler);
  },
}));

vi.mock("@/lib/wsTopicConnection", () => ({
  captureStatusConnection: { id: "capture" },
  sessionConnection: { id: "session" },
}));

import { NotificationCenter } from "@/components/operator-events/NotificationCenter";
import {
  OperatorEventsProvider,
  useOperatorEvents,
} from "@/components/operator-events/OperatorEventsProvider";

function EventCounts() {
  const {
    logs,
    markAllRead,
    notifications,
    publish,
    storageError,
  } = useOperatorEvents();
  return (
    <>
      <output aria-label="log count">{logs.length}</output>
      <output aria-label="notification count">{notifications.length}</output>
      <output aria-label="event titles">
        {[...logs, ...notifications].map(({ title }) => title).join("|")}
      </output>
      <output aria-label="storage error">{storageError ?? ""}</output>
      <button
        type="button"
        onClick={() => publish({
          source: "capture",
          severity: "warning",
          title: "Live feed is stale",
          detail: "No fresh ticks for 7 seconds.",
          isLog: true,
          isNotification: true,
        })}
      >
        Publish stale
      </button>
      <button type="button" onClick={markAllRead}>Mark all read</button>
    </>
  );
}

function RestConditionControls() {
  const { reportMonitorRestError } = useOperatorEvents();
  return (
    <>
      <button
        type="button"
        onClick={() => reportMonitorRestError("Session history request timed out.")}
      >
        Report REST error
      </button>
      <button
        type="button"
        onClick={() => reportMonitorRestError("Stats request failed with request id 42.")}
      >
        Report changed REST error
      </button>
      <button type="button" onClick={() => reportMonitorRestError(null)}>
        Report REST recovery
      </button>
    </>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  topicMocks.handlers.clear();
  topicMocks.registrations.length = 0;
  Reflect.deleteProperty(navigator, "locks");
  vi.setSystemTime("2026-07-29T10:00:00+05:30");
});

test("collects raw session logs above routes and retains repeated messages", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );

  const session = topicMocks.handlers.get("session");
  expect(session).toBeDefined();
  act(() => {
    session!({ type: "Log", payload: { message: "writer online" } });
    session!({ type: "Log", payload: { message: "writer online" } });
  });

  expect(screen.getByLabelText("log count")).toHaveTextContent("2");
  expect(screen.getByLabelText("notification count")).toHaveTextContent("0");
  expect(window.localStorage.getItem(
    "tickvault:operational-events:v1:2026-07-29",
  )).toContain("writer online");
});

test("shows a notification for exactly seven seconds and retains it newest first", () => {
  vi.useFakeTimers();
  render(
    <OperatorEventsProvider>
      <NotificationCenter />
      <EventCounts />
    </OperatorEventsProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Publish stale" }));
  const toastRegion = screen.getByLabelText("Recent notifications");
  expect(within(toastRegion).getByRole("status")).toHaveTextContent("Live feed is stale");

  act(() => vi.advanceTimersByTime(6_999));
  expect(within(toastRegion).getByRole("status")).toBeVisible();
  act(() => vi.advanceTimersByTime(1));
  expect(within(toastRegion).queryByRole("status")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /notifications/i }));
  expect(screen.getByRole("dialog", { name: "Notifications" })).toHaveTextContent(
    "Live feed is stale",
  );
  expect(screen.getByRole("button", { name: /notifications/i })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.getByRole("button", { name: /notifications/i })).toHaveAccessibleName(
    "Notifications",
  );
});

test("routes REST failure episodes and recovery into timestamped notification history", () => {
  render(
    <OperatorEventsProvider>
      <NotificationCenter />
      <EventCounts />
      <RestConditionControls />
    </OperatorEventsProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Report REST error" }));
  fireEvent.click(screen.getByRole("button", { name: "Report changed REST error" }));
  fireEvent.click(screen.getByRole("button", { name: "Report REST error" }));
  expect(screen.getByLabelText("notification count")).toHaveTextContent("1");

  fireEvent.click(screen.getByRole("button", { name: "Report REST recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Report REST error" }));
  expect(screen.getByLabelText("notification count")).toHaveTextContent("3");

  fireEvent.click(screen.getByRole("button", { name: /notifications/i }));
  const history = screen.getByRole("dialog", { name: "Notifications" });
  const items = within(history).getAllByRole("listitem");
  expect(items.map((item) => item.textContent)).toEqual([
    expect.stringContaining("Monitor REST refresh failed"),
    expect.stringContaining("Monitor REST refresh recovered"),
    expect.stringContaining("Monitor REST refresh failed"),
  ]);
  expect(history).toHaveTextContent("Today");
  expect(history).not.toHaveTextContent(/newest first/i);
  expect(history.querySelectorAll("time")).toHaveLength(3);
  expect(history).not.toHaveTextContent("request id 42");
  [...history.querySelectorAll("time")].forEach((time) => {
    expect(time).toHaveAttribute("datetime");
  });
});

test("records recovery after reloading persisted REST failure history", () => {
  const dayKey = "2026-07-29";
  window.localStorage.setItem(
    `tickvault:operational-events:v1:${dayKey}`,
    JSON.stringify({
      version: 1,
      dayKey,
      events: [{
        id: "persisted-rest-failure",
        ts: Date.now(),
        dayKey,
        source: "client",
        severity: "warning",
        title: "Monitor REST refresh failed",
        detail: "Session or capture-history data could not be refreshed. Last valid values remain on screen.",
        isLog: false,
        isNotification: true,
        isRead: false,
      }],
    }),
  );

  render(
    <OperatorEventsProvider>
      <EventCounts />
      <RestConditionControls />
    </OperatorEventsProvider>,
  );

  expect(screen.getByLabelText("notification count")).toHaveTextContent("1");
  fireEvent.click(screen.getByRole("button", { name: "Report REST recovery" }));
  expect(screen.getByLabelText("notification count")).toHaveTextContent("2");
  expect(screen.getByLabelText("event titles")).toHaveTextContent(
    "Monitor REST refresh recovered",
  );
});

test("derives one stale alert and one recovery from repeated capture snapshots", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  const capture = topicMocks.handlers.get("capture");
  const payload = (stale: boolean) => ({
    per_underlying: [],
    global: {
      fps: 1,
      stale,
      degraded: stale,
      ingestion_degraded: false,
      reconnects: 0,
      exhausted: false,
      data_age_ms: stale ? 8_000 : 0,
    },
  });

  act(() => {
    capture!({ type: "CaptureStatus", payload: payload(false) });
    capture!({ type: "CaptureStatus", payload: payload(true) });
    capture!({ type: "CaptureStatus", payload: payload(true) });
    capture!({ type: "CaptureStatus", payload: payload(false) });
  });

  expect(screen.getByLabelText("notification count")).toHaveTextContent("2");
  expect(screen.getByLabelText("log count")).toHaveTextContent("2");
});

test("records a second stale episode when counters have not changed", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  const capture = topicMocks.handlers.get("capture")!;
  const payload = (stale: boolean) => ({
    per_underlying: [],
    global: {
      fps: 1,
      stale,
      degraded: stale,
      ingestion_degraded: false,
      reconnects: 0,
      exhausted: false,
      data_age_ms: stale ? 8_000 : 0,
    },
  });

  act(() => {
    capture({ type: "CaptureStatus", payload: payload(false) });
    capture({ type: "CaptureStatus", payload: payload(true) });
    capture({ type: "CaptureStatus", payload: payload(false) });
    capture({ type: "CaptureStatus", payload: payload(true) });
  });

  expect(screen.getByLabelText("notification count")).toHaveTextContent("3");
  expect(screen.getByLabelText("event titles")).toHaveTextContent(
    "Live feed is stale|Live feed recovered|Live feed is stale",
  );
});

test("does not announce the same persisted stale incident again after reload", () => {
  window.localStorage.setItem(
    "tickvault:operational-events:v1:2026-07-29",
    JSON.stringify({
      version: 1,
      dayKey: "2026-07-29",
      events: [{
        id: "2026-07-29:capture:stale:0:0",
        ts: Date.parse("2026-07-29T09:59:00+05:30"),
        dayKey: "2026-07-29",
        source: "capture",
        severity: "danger",
        title: "Live feed is stale",
        detail: "Existing incident.",
        isLog: true,
        isNotification: true,
        isRead: true,
      }],
    }),
  );
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  const capture = topicMocks.handlers.get("capture")!;
  act(() => capture({
    type: "CaptureStatus",
    payload: {
      per_underlying: [],
      global: {
        fps: 1,
        stale: true,
        degraded: true,
        ingestion_degraded: false,
        reconnects: 0,
        exhausted: false,
        data_age_ms: 5_000,
      },
    },
  }));

  expect(screen.getByLabelText("notification count")).toHaveTextContent("1");
  expect(screen.getByLabelText("log count")).toHaveTextContent("1");
});

test("allows only one tab provider to own operational websocket subscriptions", () => {
  let isHeld = false;
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: vi.fn((_name: string, _options: unknown, callback: () => Promise<void>) => {
        if (isHeld) return new Promise(() => undefined);
        isHeld = true;
        return callback();
      }),
    },
  });

  render(
    <>
      <OperatorEventsProvider><span>first tab</span></OperatorEventsProvider>
      <OperatorEventsProvider><span>second tab</span></OperatorEventsProvider>
    </>,
  );

  expect(topicMocks.registrations).toEqual(["capture", "session"]);
});

test("falls back to the storage lease when Web Locks rejects", async () => {
  window.localStorage.setItem(
    "tickvault:operational-event-leader:v1",
    JSON.stringify({ owner: "another-tab", expiresAt: Date.now() + 10_000 }),
  );
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: vi.fn(() => Promise.reject(new Error("locks unavailable"))),
    },
  });

  render(
    <OperatorEventsProvider>
      <span>waiting tab</span>
    </OperatorEventsProvider>,
  );
  await act(async () => Promise.resolve());

  expect(topicMocks.registrations).toEqual([]);
});

test("honors the cross-tab localStorage lease when Web Locks are unavailable", () => {
  window.localStorage.setItem(
    "tickvault:operational-event-leader:v1",
    JSON.stringify({ owner: "another-tab", expiresAt: Date.now() + 10_000 }),
  );

  render(
    <OperatorEventsProvider>
      <span>waiting tab</span>
    </OperatorEventsProvider>,
  );

  expect(topicMocks.registrations).toEqual([]);
});

test("validates session messages and announces meaningful phase changes once", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  const session = topicMocks.handlers.get("session")!;

  act(() => {
    session({ type: "Log", payload: { message: 42 } });
    session({ type: "Log", payload: { message: "  " } });
    session({ type: "Unrelated", payload: {} });
    session({ type: "SessionStatus", payload: { phase: "connected" } });
    session({ type: "SessionStatus", payload: { phase: "capturing" } });
    session({ type: "SessionStatus", payload: { phase: "capturing" } });
    session({ type: "SessionStatus", payload: { phase: "failed" } });
  });

  expect(screen.getByLabelText("log count")).toHaveTextContent("3");
  expect(screen.getByLabelText("notification count")).toHaveTextContent("2");
  expect(screen.getByLabelText("event titles")).toHaveTextContent("Session: capturing");
  expect(screen.getByLabelText("event titles")).toHaveTextContent("Session: failed");
});

test("announces compression completion and failure but ignores malformed updates", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  const capture = topicMocks.handlers.get("capture")!;
  const compression = (phase: string) => ({
    phase,
    files_done: 4,
    files_total: 4,
    current_file: phase === "failed" ? "NIFTY.bin" : null,
  });

  act(() => {
    capture({ type: "Other", payload: {} });
    capture({ type: "CompressionProgress", payload: "invalid" });
    capture({ type: "CompressionProgress", payload: compression("running") });
    capture({ type: "CompressionProgress", payload: compression("done") });
    capture({ type: "CompressionProgress", payload: compression("done") });
    capture({ type: "CompressionProgress", payload: compression("failed") });
  });

  expect(screen.getByLabelText("notification count")).toHaveTextContent("2");
  expect(screen.getByLabelText("event titles")).toHaveTextContent("Compression completed");
  expect(screen.getByLabelText("event titles")).toHaveTextContent("Compression failed");
});

test("records repeated compression completion episodes with legacy zero timestamps", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  const capture = topicMocks.handlers.get("capture")!;
  const compression = (phase: string) => ({
    phase,
    files_done: 4,
    files_total: 4,
    current_file: null,
    started_at: 0,
  });

  act(() => {
    capture({ type: "CompressionProgress", payload: compression("running") });
    capture({ type: "CompressionProgress", payload: compression("done") });
    capture({ type: "CompressionProgress", payload: compression("running") });
    capture({ type: "CompressionProgress", payload: compression("done") });
  });

  expect(screen.getByLabelText("notification count")).toHaveTextContent("2");
});

test("suppresses a repeated initial session phase after leader handoff", () => {
  window.localStorage.setItem(
    "tickvault:operational-events:v1:2026-07-29",
    JSON.stringify({
      version: 1,
      dayKey: "2026-07-29",
      events: [{
        id: "persisted-session-phase",
        ts: Date.parse("2026-07-29T09:59:00+05:30"),
        dayKey: "2026-07-29",
        source: "session",
        severity: "success",
        title: "Session: capturing",
        detail: "",
        isLog: true,
        isNotification: true,
        isRead: true,
      }],
    }),
  );
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );

  act(() => topicMocks.handlers.get("session")!({
    type: "SessionStatus",
    payload: { phase: "capturing" },
  }));

  expect(screen.getByLabelText("notification count")).toHaveTextContent("1");
});

test("shows a seven-second toast in a follower tab when storage receives an event", () => {
  vi.useFakeTimers();
  window.localStorage.setItem(
    "tickvault:operational-event-leader:v1",
    JSON.stringify({ owner: "another-tab", expiresAt: Date.now() + 10_000 }),
  );
  render(
    <OperatorEventsProvider>
      <NotificationCenter />
    </OperatorEventsProvider>,
  );
  const event = {
    id: "remote-stale",
    ts: Date.now(),
    dayKey: "2026-07-29",
    source: "capture",
    severity: "danger",
    title: "Live feed is stale",
    detail: "Remote collector observed stale data.",
    isLog: true,
    isNotification: true,
    isRead: false,
  };
  const key = "tickvault:operational-events:v1:2026-07-29";
  const value = JSON.stringify({
    version: 1,
    dayKey: "2026-07-29",
    events: [event],
  });
  window.localStorage.setItem(key, value);
  act(() => window.dispatchEvent(new StorageEvent("storage", {
    key,
    newValue: value,
  })));

  expect(within(screen.getByLabelText("Recent notifications")).getByRole("alert"))
    .toHaveTextContent("Live feed is stale");
  act(() => vi.advanceTimersByTime(7_000));
  expect(within(screen.getByLabelText("Recent notifications")).queryByRole("alert"))
    .not.toBeInTheDocument();
});

test("does not replay an expired follower notification after tab suspension", () => {
  vi.useFakeTimers();
  window.localStorage.setItem(
    "tickvault:operational-event-leader:v1",
    JSON.stringify({ owner: "another-tab", expiresAt: Date.now() + 10_000 }),
  );
  render(
    <OperatorEventsProvider>
      <NotificationCenter />
    </OperatorEventsProvider>,
  );
  const key = "tickvault:operational-events:v1:2026-07-29";
  const value = JSON.stringify({
    version: 1,
    dayKey: "2026-07-29",
    events: [{
      id: "expired-remote-stale",
      ts: Date.now() - 7_001,
      dayKey: "2026-07-29",
      source: "capture",
      severity: "danger",
      title: "Live feed is stale",
      detail: "This alert was delivered while the tab was suspended.",
      isLog: true,
      isNotification: true,
      isRead: false,
    }],
  });
  window.localStorage.setItem(key, value);
  act(() => window.dispatchEvent(new StorageEvent("storage", {
    key,
    newValue: value,
  })));

  expect(within(screen.getByLabelText("Recent notifications")).queryByRole("alert"))
    .not.toBeInTheDocument();
});

test("marking notifications read preserves events that arrived in storage", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish stale" }));
  const key = "tickvault:operational-events:v1:2026-07-29";
  const firstPayload = JSON.parse(window.localStorage.getItem(key)!) as {
    events: Array<Record<string, unknown>>;
  };
  const remoteEvent = {
    ...firstPayload.events[0],
    id: "remote-event",
    ts: Date.now() + 1,
    title: "Remote event",
    isRead: false,
  };
  window.localStorage.setItem(key, JSON.stringify({
    version: 1,
    dayKey: "2026-07-29",
    events: [...firstPayload.events, remoteEvent],
  }));

  fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));

  const saved = JSON.parse(window.localStorage.getItem(key)!) as {
    events: Array<{ id: string; isRead: boolean }>;
  };
  expect(saved.events.map(({ id }) => id)).toContain("remote-event");
  expect(saved.events.every(({ isRead }) => isRead)).toBe(true);
});

test("clears the previous day atomically when the workstation day advances", () => {
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish stale" }));
  expect(screen.getByLabelText("log count")).toHaveTextContent("1");

  vi.setSystemTime("2026-07-30T00:00:01+05:30");
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  act(() => document.dispatchEvent(new Event("visibilitychange")));

  expect(screen.getByLabelText("log count")).toHaveTextContent("0");
  expect(screen.getByLabelText("notification count")).toHaveTextContent("0");
  expect(window.localStorage.getItem(
    "tickvault:operational-events:v1:2026-07-29",
  )).toBeNull();
});

test("surfaces storage failures while retaining the event in memory", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("quota", "QuotaExceededError");
  });
  render(
    <OperatorEventsProvider>
      <EventCounts />
    </OperatorEventsProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish stale" }));

  expect(screen.getByLabelText("log count")).toHaveTextContent("1");
  expect(screen.getByLabelText("storage error")).toHaveTextContent(
    "could not be saved",
  );
});

test("closes notification history with Escape and restores trigger focus", () => {
  render(
    <OperatorEventsProvider>
      <NotificationCenter />
      <EventCounts />
    </OperatorEventsProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /notifications/i }));
  expect(screen.getByRole("dialog", { name: "Notifications" })).toHaveFocus();

  fireEvent.keyDown(document, { key: "Escape" });
  const trigger = screen.getByRole("button", { name: /notifications/i });
  expect(screen.queryByRole("dialog", { name: "Notifications" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
