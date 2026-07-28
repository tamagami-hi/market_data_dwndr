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
