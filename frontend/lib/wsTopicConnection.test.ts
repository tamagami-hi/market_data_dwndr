import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const opened: FakeWebSocket[] = [];

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readonly url: string;
  readyState = FakeWebSocket.OPEN;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string | URL) {
    this.url = String(url);
    opened.push(this);
  }

  close() {
    this.closed = true;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  send() {}
}

vi.stubGlobal("WebSocket", FakeWebSocket);

const connections = await import("@/lib/wsTopicConnection");

beforeEach(() => {
  opened.length = 0;
});

afterEach(() => {
  vi.clearAllTimers();
});

describe("WebSocket stream hygiene", () => {
  test("many consumers of one topic share a single socket", () => {
    const connection = connections.marketDataConnection;
    connection.acquire();
    connection.acquire();
    connection.acquire();

    expect(opened).toHaveLength(1);
    connection.release();
    connection.release();
    expect(opened[0].closed).toBe(false);
    connection.release();
    expect(opened[0].closed).toBe(true);
  });

  test("re-acquiring after full release opens exactly one new socket", () => {
    const connection = connections.stocksConnection;
    connection.acquire();
    connection.release();
    connection.acquire();

    expect(opened).toHaveLength(2);
    expect(opened[1].closed).toBe(false);
    connection.release();
  });

  test("exports distinct topic connections", () => {
    const topics = [
      connections.marketDataConnection,
      connections.stocksConnection,
      connections.captureStatusConnection,
      connections.sessionConnection,
      connections.historicalJobsConnection,
    ];
    expect(new Set(topics).size).toBe(topics.length);
  });

  test("does not connect an unacquired topic", () => {
    connections.historicalJobsConnection.getState();
    expect(opened).toHaveLength(0);
  });
});


describe("stale-connection watchdog", () => {
  // The freeze this exists for: the socket stays OPEN and the server stops delivering,
  // so onclose/onerror never fire and reconnect was never triggered. The UI sat at
  // connected:true with frozen numbers indefinitely.
  beforeEach(() => {
    vi.useFakeTimers();
    opened.length = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function deliver(socket: FakeWebSocket, payload: unknown = { type: "heartbeat" }) {
    socket.onmessage?.({ data: JSON.stringify(payload) });
  }

  test("closes a silent-but-open socket so the existing reconnect logic takes over", () => {
    const connection = connections.__createTopicConnectionForTest("market-data", {
      watchdog: true,
    });
    connection.acquire();
    const socket = opened[0];
    socket.onopen?.();
    deliver(socket); // arm the watchdog with real traffic

    // Silence shorter than the watchdog threshold must NOT close anything.
    vi.advanceTimersByTime(connections.WATCHDOG_SILENCE_MS - 1_000);
    expect(socket.closed).toBe(false);

    // Crossing the threshold force-closes, and onclose schedules a reconnect.
    vi.advanceTimersByTime(2_000);
    expect(socket.closed).toBe(true);

    vi.advanceTimersByTime(1_000); // backoff for the first retry
    expect(opened.length).toBe(2);
    expect(opened[1].closed).toBe(false);

    connection.release();
  });

  test("normal live traffic never trips the watchdog", () => {
    const connection = connections.__createTopicConnectionForTest("stocks", {
      watchdog: true,
    });
    connection.acquire();
    const socket = opened[0];
    socket.onopen?.();

    // A 1 Hz feed for well past the threshold.
    for (let i = 0; i < connections.WATCHDOG_SILENCE_MS / 1_000 + 10; i += 1) {
      deliver(socket);
      vi.advanceTimersByTime(1_000);
    }

    expect(socket.closed).toBe(false);
    expect(opened).toHaveLength(1);
    connection.release();
  });

  test("a silence episode costs at most one forced reconnect", () => {
    // Otherwise a legitimately quiet topic (market closed) would be reopened forever,
    // since a successful open resets the retry counter.
    const connection = connections.__createTopicConnectionForTest("capture-status", {
      watchdog: true,
    });
    connection.acquire();
    opened[0].onopen?.();
    deliver(opened[0]);

    vi.advanceTimersByTime(connections.WATCHDOG_SILENCE_MS + 1_000);
    expect(opened[0].closed).toBe(true);
    vi.advanceTimersByTime(1_000);
    expect(opened).toHaveLength(2);
    opened[1].onopen?.();

    // The replacement stays silent: no further watchdog closes without fresh data.
    vi.advanceTimersByTime(connections.WATCHDOG_SILENCE_MS * 3);
    expect(opened[1].closed).toBe(false);
    expect(opened).toHaveLength(2);

    connection.release();
  });

  test("never fires without a watchdog opt-in (event-driven topics)", () => {
    const connection = connections.__createTopicConnectionForTest("historical-jobs");
    connection.acquire();
    const socket = opened[0];
    socket.onopen?.();
    deliver(socket);

    vi.advanceTimersByTime(connections.WATCHDOG_SILENCE_MS * 5);

    expect(socket.closed).toBe(false);
    connection.release();
  });

  test("does not fire before any message has ever arrived", () => {
    const connection = connections.__createTopicConnectionForTest("session", {
      watchdog: true,
    });
    connection.acquire();
    const socket = opened[0];
    socket.onopen?.();

    vi.advanceTimersByTime(connections.WATCHDOG_SILENCE_MS * 3);

    expect(socket.closed).toBe(false);
    connection.release();
  });
});
