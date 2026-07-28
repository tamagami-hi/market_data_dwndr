import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import type { GridBlock, StockBoardPayload } from "@/lib/wsTypes";

const mocks = vi.hoisted(() => ({
  handlers: [] as Array<(envelope: { type: string; payload?: unknown }) => void>,
  path: "/monitor",
  telemetry: null as unknown,
  connection: {
    connected: true,
    ageMs: 100,
    pipelineMs: 12,
    greeksMs: 5,
    stocksMs: 4,
    bytesPerSec: 2_048,
    error: null,
  },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.path,
}));

vi.mock("@/lib/useTopic", () => ({
  useConnectionState: () => mocks.connection,
  useTopicEnvelopes: (
    _connection: unknown,
    handler: (envelope: { type: string; payload?: unknown }) => void,
  ) => {
    mocks.handlers.push(handler);
  },
}));

vi.mock("@/hooks/useMonitorTelemetry", () => ({
  useMonitorTelemetry: () => mocks.telemetry,
}));

vi.mock("@/hooks/polling", () => ({
  createPollController: (options: { task: (signal: AbortSignal) => Promise<void> }) => ({
    start: () => void options.task(new AbortController().signal),
    stop: vi.fn(),
    resume: vi.fn(),
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    getStats: vi.fn().mockResolvedValue({
      capture_running: true,
      trading_date: "2026-07-29",
      market_phase: "open",
    }),
    getAuthStatus: vi.fn().mockResolvedValue({
      configured: true,
      authenticated: true,
      external_token_source_configured: true,
      capture_ready: true,
      trading_date: "2026-07-29",
      market_phase: "open",
      risk_free_rate: 0.06,
      capture: { available: true, running: true, tokens: 400, trading_date: "2026-07-29" },
      automation: { phase: "capture_window", last_action: "Capture online" },
    }),
  };
});

import DownloaderPage from "@/app/login/page";
import MonitorLayout from "@/app/monitor/layout";
import MonitorPage from "@/app/monitor/page";
import OptionLayout from "@/app/option-chain/layout";
import OptionChainPage from "@/app/option-chain/page";
import HomePage from "@/app/page";
import StocksLayout from "@/app/stocks/layout";
import StocksPage from "@/app/stocks/page";
import RootLayout from "@/app/layout";
import LoginLayout from "@/app/login/layout";
import ConnectionDot from "@/components/ConnectionDot";
import NavBar from "@/components/NavBar";
import { MonitorAlerts } from "@/components/monitor/MonitorAlerts";
import { Skeleton } from "@/components/ui/Skeleton";
import { normalizeCaptureStatus } from "@/lib/monitor/viewModel";
import { captureStatusConnection } from "@/lib/wsTopicConnection";

function optionBlock(offset: number): GridBlock {
  return {
    oi: [1 + offset],
    change_in_oi: [2 + offset],
    volume: [3 + offset],
    iv: [4 + offset],
    delta: [0.5],
    gamma: [0.01],
    theta: [-1],
    vega: [2],
    rho: [1],
    bid: [10 + offset],
    ask: [11 + offset],
    ltp: [12 + offset],
    change: [1],
  };
}

function stockBoard(): StockBoardPayload {
  const scalars = (value: number) => ({
    ltp: [value],
    oi: [10],
    volume: [20],
    buy_quantity: [30],
    sell_quantity: [40],
    oi_day_high: [50],
    oi_day_low: [5],
    ohlc_open: [value],
    ohlc_high: [value + 1],
    ohlc_low: [value - 1],
    ohlc_close: [value],
  });
  const depth = (value: number) => Array.from({ length: 5 }, () => ({
    bid_price: [value - 1],
    bid_qty: [10],
    bid_orders: [1],
    ask_price: [value + 1],
    ask_qty: [20],
    ask_orders: [2],
  }));
  return {
    timestamp: Date.now(),
    count: 1,
    tradingsymbols: ["RELIANCE"],
    names: ["Reliance Industries"],
    future_expiries: [["2026-07-30", "2026-08-27", "2026-09-24"]],
    legs: {
      spot: { scalars: scalars(2_450), depth: depth(2_450) },
      fut_current: { scalars: scalars(2_460), depth: depth(2_460) },
      fut_mid: { scalars: scalars(2_470), depth: depth(2_470) },
      fut_far: { scalars: scalars(2_480), depth: depth(2_480) },
    },
    live_spread: [10],
    daily_spread: [-2],
  };
}

function richTelemetry() {
  const capture = normalizeCaptureStatus({
    per_underlying: [{
      underlying: "NIFTY",
      connected: true,
      frames_written: 100,
      frames_expected: 110,
      frame_loss_pct: 1,
      session_frames_expected: 100,
      session_loss_pct: 0.1,
      day_complete_pct: 50,
      file_bytes: 1_000,
      avg_bytes_per_frame: 10,
      projected_eod_bytes: 2_000,
      heartbeat_ok: true,
      heartbeat_age_ms: 100,
      data_fresh: true,
      unmatched: 2,
      applied: 20,
      writer_pending: 1,
    }],
    global: {
      fps: 4,
      tokens: 400,
      disk_bytes: 1_000,
      disk_free_bytes: 1_000,
      disk_total_bytes: 2_000,
      captures: 100,
      dropped_batches: 1,
      drop_rate_pct: 0.01,
      ingestion_degraded: true,
      uptime_ms: 60_000,
      frames_written: 100,
      frames_expected: 110,
      frame_loss_pct: 1,
      snapshot_ms: 3,
      writer_lag_max: 1,
      data_age_ms: 10_000,
      liveness_age_ms: 100,
      stale: true,
      degraded: true,
      frozen_batches: 1,
      reconnects: 2,
      exhausted: true,
      grid_gaps: 2,
      grid_seconds_lost: 3,
      frozen_seconds: 4,
      unmatched_ticks: 5,
      ticks_received: 6_000,
      disk_runway_hours: 2,
    },
  });
  const session = {
    trading_date: "2026-07-28",
    recorded_at: 1,
    uptime_ms: 60_000,
    captures: 100,
    frames_written: 100,
    frames_expected: 110,
    frame_loss_pct: 1,
    session_frames_expected: 100,
    session_loss_pct: 0.1,
    grid_gaps: 2,
    grid_seconds_lost: 3,
    frozen_seconds: 4,
    dropped_batches: 1,
    drop_rate_pct: 0.01,
    unmatched_ticks: 5,
    ticks_received: 6_000,
    reconnects: 2,
    token_refreshes: 1,
    exhausted: true,
    disk_bytes: 1_000,
    streams: [{ underlying: "NIFTY", frames_written: 100, frame_loss_pct: 0.1, file_bytes: 1_000 }],
  };
  return {
    live: {
      rows: capture?.per_underlying ?? [],
      globals: capture?.global ?? null,
      compression: {
        phase: "running",
        files_done: 1,
        files_total: 2,
        bytes_done: 100,
        bytes_total: 200,
        zst_bytes: 50,
        ratio: 2,
        current_file: "nifty.bin",
        threads: 2,
        started_at: 1,
        updated_at: 2,
        elapsed_ms: 1_000,
        file_elapsed_ms: 500,
        avg_file_ms: 500,
        throughput_mbps: 10,
      },
      logs: [{ id: 1, ts: Date.now(), text: "capture ready", kind: "session" }],
      fpsHistory: [1, 2, 3],
    },
    history: {
      sessions: [session],
      capture: {
        available: true,
        generated_at: 1,
        totals: { sessions: 1, total_bytes: 1_000, raw_bytes: 500, archived_bytes: 500, data_files: 2 },
        sessions: [{
          trading_date: "2026-07-29",
          is_current: true,
          total_bytes: 1_000,
          raw_bytes: 500,
          archived_bytes: 500,
          data_files: 2,
          raw_files: 1,
          archived_files: 1,
          index_files: 1,
          stock_files: 1,
          indices: ["NIFTY"],
        }],
      },
      compression: {
        samples: 1,
        avg_ratio: 2,
        avg_total_elapsed_ms: 1_000,
        avg_file_ms: 500,
        avg_throughput_mbps: 10,
        last: null,
      },
    },
    context: {
      captureRunning: true,
      tradingDate: "2026-07-29",
      shownDate: "2026-07-28",
      refreshWindow: { auth_poll_start: "08:30", auth_poll_end: "09:00", in_auth_window: true, should_refresh: true, local_time: "09:15" },
      expectedFrames: 23_400,
    },
    freshness: {
      lastSuccessAt: Date.now(),
      restError: "History retrying.",
      payloadError: "One payload was rejected.",
      isRestStale: true,
    },
    source: { type: "persisted", isPastSession: true },
  };
}

beforeEach(() => {
  mocks.handlers.length = 0;
  mocks.path = "/monitor";
  mocks.telemetry = richTelemetry();
  mocks.connection = {
    connected: true,
    ageMs: 100,
    pipelineMs: 12,
    greeksMs: 5,
    stocksMs: 4,
    bytesPerSec: 2_048,
    error: null,
  };
});

test("renders the application shell, route metadata layouts, and launchpad", () => {
  render(<NavBar />);
  expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  const home = render(<HomePage />);
  expect(screen.getByRole("heading", { name: "TickVault" })).toBeInTheDocument();
  expect(home.container.firstElementChild).toHaveClass("page-frame");
  expect(screen.getByRole("link", { name: /Open Capture Monitor/ })).toHaveClass("lg:col-span-12");
  expect(screen.getByRole("link", { name: /Open Option Chain/ })).toHaveClass("lg:col-span-4");
  expect(screen.getByRole("link", { name: /Open Stocks Board/ })).toHaveClass("lg:col-span-4");
  expect(screen.getByRole("link", { name: /Open Downloader/ })).toHaveClass("lg:col-span-4");
  expect(RootLayout({ children: <span>child</span> }).type).toBe("html");
  expect(MonitorLayout({ children: <span /> })).toBeTruthy();
  expect(OptionLayout({ children: <span /> })).toBeTruthy();
  expect(StocksLayout({ children: <span /> })).toBeTruthy();
  expect(LoginLayout({ children: <span /> })).toBeTruthy();
  render(<Skeleton className="h-4" />);
});

test("renders rich monitor sections, alerts, disclosures, and native logs dialog", async () => {
  const user = userEvent.setup();
  const view = render(<MonitorPage />);
  expect(view.container.firstElementChild).toHaveClass("page-frame");
  expect(screen.getByRole("heading", { name: "Capture Monitor" })).toBeInTheDocument();
  expect(screen.getAllByText("Recovery exhausted").length).toBeGreaterThan(0);
  expect(screen.getAllByText("NIFTY").length).toBeGreaterThan(0);
  await user.click(screen.getByRole("button", { name: "Open log viewer" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  const degradedGlobals = {
    ...(mocks.telemetry as ReturnType<typeof richTelemetry>).live.globals!,
    stale: false,
    exhausted: false,
    degraded: true,
    ingestion_degraded: false,
  };
  mocks.telemetry = {
    live: { rows: [], globals: null, compression: null, logs: [], fpsHistory: [] },
    history: { sessions: [], capture: null, compression: null },
    context: {
      captureRunning: false,
      tradingDate: null,
      shownDate: null,
      refreshWindow: null,
      expectedFrames: 23_400,
    },
    freshness: {
      lastSuccessAt: null,
      restError: null,
      payloadError: null,
      isRestStale: false,
    },
    source: { type: "none", isPastSession: false },
  };
  view.rerender(<MonitorPage />);
  expect(screen.getByText("Capture history unavailable")).toBeInTheDocument();
  expect(screen.getAllByText("No session messages yet.").length).toBeGreaterThan(0);
  render(<MonitorAlerts globals={degradedGlobals} restError={null} payloadError={null} isRestStale />);
  expect(screen.getByText("Capture is degraded")).toBeInTheDocument();
});

test("balances single monitor alerts and paired monitor panels", () => {
  const alerts = render(
    <MonitorAlerts globals={null} restError="History retrying." payloadError={null} isRestStale={false} />,
  );
  expect(alerts.getByRole("alert").parentElement).toHaveClass("lg:col-span-2");
  alerts.rerender(
    <MonitorAlerts
      globals={null}
      restError="History retrying."
      payloadError="One telemetry payload was rejected because it was malformed."
      isRestStale={false}
    />,
  );
  alerts.getAllByRole("alert").forEach((alert) => expect(alert).toHaveClass("h-full"));

  const monitor = render(<MonitorPage />);
  const healthPanel = screen.getByRole("heading", { name: "Per-underlying health" }).closest(".panel");
  const lossPanel = screen.getByRole("heading", { name: "Data-loss diagnostics" }).closest(".panel");
  const storagePanel = screen.getByRole("heading", { name: "Download history" }).closest(".panel");
  const compressionPanel = screen.getByRole("heading", { name: "Compression" }).closest(".panel");
  [healthPanel, lossPanel, storagePanel, compressionPanel].forEach((panel) => {
    expect(panel).toHaveClass("h-full");
  });
  monitor.unmount();
});

test("renders option-chain snapshots, deltas, symbols, and malformed recovery", async () => {
  const user = userEvent.setup();
  const view = render(<OptionChainPage />);
  expect(view.container.firstElementChild).toHaveClass("page-frame");
  expect(screen.getByText("Awaiting underlyings")).toBeInTheDocument();
  const handler = mocks.handlers.at(-1)!;
  act(() => {
    handler({ type: "MarketHeader", payload: { underlying: "NIFTY", expiry: "2026-07-30", spot: 22_000, atm: 22_000, vix: 12, risk_free_rate: 0.06, timestamp: 1, sequence: 1 } });
  });
  expect(screen.getByText("Max Pain")).toBeInTheDocument();
  expect(screen.getAllByText("--").length).toBeGreaterThan(0);
  act(() => {
    handler({ type: "OptionGrid", payload: { underlying: "NIFTY", expiry: "2026-07-30", strikes: [22_000], calls: optionBlock(0), puts: optionBlock(100), market_atm: 22_000, max_pain: 22_000, spot_atm: 22_000, spot: 22_010, vix: 12 } });
    handler({ type: "OptionGridDelta", payload: { underlying: "NIFTY", changed_indices: [0], calls: { ltp: [99] }, puts: {} } });
    handler({ type: "OptionGridDelta", payload: null });
    handler({ type: "OptionGrid", payload: { underlying: "CUSTOM", expiry: "2026-07-30", strikes: [22_000], calls: optionBlock(0), puts: optionBlock(100), market_atm: 22_000, max_pain: 22_000, spot_atm: 22_000, spot: 22_010, vix: 12 } });
    handler({ type: "OptionGrid", payload: { underlying: "BANKNIFTY", expiry: "2026-07-30", strikes: [22_000], calls: optionBlock(0), puts: optionBlock(100), market_atm: 22_000, max_pain: 22_000, spot_atm: 22_000, spot: 22_010, vix: 12 } });
  });
  expect(await screen.findByText("NIFTY")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "CUSTOM" }));
  await user.click(screen.getByRole("button", { name: "Strike 22000 details" }));
  expect(screen.getByRole("heading", { name: "Greeks" })).toBeInTheDocument();
});

test("renders stock summaries, symbol filtering, spreads, and complete expansion", async () => {
  const user = userEvent.setup();
  const view = render(<StocksPage />);
  expect(view.container.firstElementChild).toHaveClass("page-frame");
  act(() => mocks.handlers.at(-1)!({ type: "StockBoard", payload: stockBoard() }));
  expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Spot 2,450/ }));
  expect(screen.getAllByRole("region", { name: /L5 market depth/ }).length).toBeGreaterThan(0);
  await user.type(screen.getByPlaceholderText("Filter symbol"), "missing");
  expect(screen.getByText("No symbols match the filter")).toBeInTheDocument();
  act(() => mocks.handlers.at(-1)!({ type: "StockBoard", payload: { count: 1 } }));
});

test("renders downloader automation and all connection states", async () => {
  const { container, rerender } = render(<DownloaderPage />);
  expect(container.firstElementChild).toHaveClass("page-frame");
  expect(await screen.findByText("Downloader is running")).toBeInTheDocument();
  render(<ConnectionDot connection={captureStatusConnection} label="capture" />);
  expect(screen.getAllByText(/capture: connected/).length).toBeGreaterThan(0);
  mocks.connection = { ...mocks.connection, ageMs: 6_000 };
  rerender(<ConnectionDot connection={captureStatusConnection} label="capture" />);
  expect(screen.getByText(/capture: stale/)).toBeInTheDocument();
  mocks.connection = { ...mocks.connection, connected: false };
  rerender(<ConnectionDot connection={captureStatusConnection} label="capture" />);
  expect(screen.getByText(/capture: offline/)).toBeInTheDocument();
});
