import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  states: {
    capture: { bytesPerSec: 1_024, pipelineMs: 10, greeksMs: 4, stocksMs: 3 },
    options: { bytesPerSec: 2_048, pipelineMs: 11, greeksMs: 5, stocksMs: 3 },
    stocks: { bytesPerSec: 3_072, pipelineMs: 12, greeksMs: 5, stocksMs: 4 },
    session: { bytesPerSec: 128, pipelineMs: null, greeksMs: null, stocksMs: null },
  },
}));

vi.mock("@/lib/wsTopicConnection", () => ({
  captureStatusConnection: {
    topic: "capture", acquire: vi.fn(), release: vi.fn(), onEnvelope: vi.fn(() => vi.fn()),
  },
  marketDataConnection: {
    topic: "options", acquire: vi.fn(), release: vi.fn(), onEnvelope: vi.fn(() => vi.fn()),
  },
  stocksConnection: {
    topic: "stocks", acquire: vi.fn(), release: vi.fn(), onEnvelope: vi.fn(() => vi.fn()),
  },
  sessionConnection: {
    topic: "session", acquire: vi.fn(), release: vi.fn(), onEnvelope: vi.fn(() => vi.fn()),
  },
}));

vi.mock("@/lib/useTopic", () => ({
  useConnectionState: (connection: { topic: keyof typeof mocks.states }) => ({
    connected: true,
    ageMs: 100,
    error: null,
    ...mocks.states[connection.topic],
  }),
}));

import { MonitorConnections } from "@/components/monitor/MonitorConnections";
import { marketDataConnection, stocksConnection } from "@/lib/wsTopicConnection";

describe("MonitorConnections", () => {
  it("uses each page topic for its own payload throughput", async () => {
    const user = userEvent.setup();
    const view = render(<MonitorConnections />);

    expect(marketDataConnection.acquire).toHaveBeenCalledOnce();
    expect(stocksConnection.acquire).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "Explain monitor connections" }));

    const note = screen.getByRole("note");
    const sectionFor = (name: string) =>
      within(note).getByRole("heading", { name }).closest("section")!;
    expect(sectionFor("Capture Monitor")).toHaveTextContent("1.0 KB/s");
    expect(sectionFor("Option Chain")).toHaveTextContent("2.0 KB/s");
    expect(sectionFor("Stocks Board")).toHaveTextContent("3.0 KB/s");
    expect(sectionFor("Session")).toHaveTextContent("128 B/s");

    view.unmount();
    expect(marketDataConnection.release).toHaveBeenCalledOnce();
    expect(stocksConnection.release).toHaveBeenCalledOnce();
  });
});
