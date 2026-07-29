import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test, { after, before } from "node:test";

import puppeteer from "puppeteer";

const FRONTEND_PORT = requirePort("E2E_FRONTEND_PORT");
const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`;
const BACKEND_ORIGIN = requireOrigin("NEXT_PUBLIC_BACKEND_URL");
let frontendProcess;
let browser;

before(async () => {
  frontendProcess = spawn(
    "node",
    ["node_modules/next/dist/bin/next", "start", "-p", String(FRONTEND_PORT)],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        HOSTNAME: "127.0.0.1",
      },
      stdio: "ignore",
    },
  );
  await waitForServer(`${FRONTEND_URL}/login`);
  browser = await puppeteer.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
});

after(async () => {
  await browser?.close();
  frontendProcess?.kill("SIGTERM");
});

test("shows validated automatic initialization and running capture", async () => {
  const page = await browser.newPage();
  const seenRequests = await mockBackend(page);

  await page.goto(`${FRONTEND_URL}/login`, { waitUntil: "networkidle0" });
  await waitForText(page, "Downloader", seenRequests);
  await waitForText(page, "Downloader is running", seenRequests);
  await waitForText(page, "Token fetch and validation", seenRequests);
  await waitForText(page, "100%", seenRequests);

  assert.ok(seenRequests.includes("GET /api/auth/status"));
  await page.close();
});

test("expands a stock row to show all five market-depth levels", async () => {
  const page = await browser.newPage();
  await installMockWebSocket(page);
  await page.goto(`${FRONTEND_URL}/stocks`, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => Boolean(window.__stockSocket));

  await page.evaluate((message) => {
    window.__stockSocket.onmessage?.({ data: JSON.stringify(message) });
  }, stockBoardMessage());

  await waitForText(page, "RELIANCE");
  const toggle = await page.$('button[aria-controls^="desktop-stock-RELIANCE"]');
  assert.ok(toggle, "stock depth toggle should be accessible by name");
  await toggle.focus();
  await page.keyboard.press("Enter");

  await waitForText(page, "Spot L1-L5");
  await waitForText(page, "Current future L1-L5");
  await waitForText(page, "2,459.7");
  await waitForText(page, "2,460.3");
  const depthLevelCounts = await page.$$eval(
    '[aria-label="RELIANCE L5 market depth"] section',
    (sections) => sections
      .filter((section) => section.getBoundingClientRect().width > 0)
      .map((section) => section.querySelectorAll("[data-depth-level]").length),
  );
  assert.deepEqual(depthLevelCounts, [5, 5, 5, 5]);
  assert.equal(await toggle.evaluate((element) => element.getAttribute("aria-expanded")), "true");
  await page.close();
});

async function installMockWebSocket(page) {
  await page.evaluateOnNewDocument(() => {
    class MockWebSocket {
      constructor(url) {
        this.url = String(url);
        if (this.url.endsWith("/ws/stocks")) {
          window.__stockSocket = this;
        }
        setTimeout(() => this.onopen?.({}), 0);
      }

      close() {}
    }
    window.WebSocket = MockWebSocket;
  });
}

function stockBoardMessage() {
  const scalar = (value) => ({
    ltp: [value],
    oi: [8000],
    volume: [1200],
    buy_quantity: [500],
    sell_quantity: [450],
    oi_day_high: [9000],
    oi_day_low: [7000],
    ohlc_open: [value - 2],
    ohlc_high: [value + 5],
    ohlc_low: [value - 5],
    ohlc_close: [value - 1],
  });
  const depth = (base) => Array.from({ length: 5 }, (_, index) => ({
    bid_price: [base - 0.1 - index * 0.05],
    bid_qty: [100 + index],
    bid_orders: [index + 1],
    ask_price: [base + 0.1 + index * 0.05],
    ask_qty: [200 + index],
    ask_orders: [index + 2],
  }));
  return {
    type: "StockBoard",
    payload: {
      timestamp: Date.now(),
      count: 1,
      tradingsymbols: ["RELIANCE"],
      names: ["RELIANCE"],
      future_expiries: [["2026-07-30", "2026-08-27", "2026-09-24"]],
      legs: {
        spot: { scalars: scalar(2455.5), depth: depth(2455.5) },
        fut_current: { scalars: scalar(2460), depth: depth(2460) },
        fut_mid: { scalars: scalar(2475), depth: depth(2475) },
        fut_far: { scalars: scalar(2488), depth: depth(2488) },
      },
      live_spread: [15],
      daily_spread: [13],
    },
  };
}

async function mockBackend(page) {
  const seenRequests = [];
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== BACKEND_ORIGIN) {
      request.continue();
      return;
    }
    seenRequests.push(`${request.method()} ${url.pathname}`);

    if (url.pathname === "/api/auth/status") {
      respondJson(request, {
        configured: true,
        authenticated: true,
        trading_date: "2026-07-22",
        market_phase: "OPEN",
        credentials_present: true,
        external_token_source_configured: true,
        risk_free_rate: 0.0691,
        risk_free_rate_as_of: "2026-07-22",
        capture_ready: true,
        automation: { phase: "capture_window", last_action: "START_CAPTURE" },
        capture: {
          available: true,
          running: true,
          trading_date: "2026-07-22",
          indices: ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"],
          stocks: 185,
          tokens: 1548,
          skipped_indices: [],
          error: null,
        },
      });
      return;
    }
    request.abort();
  });
  return seenRequests;
}

function respondJson(request, body, status = 200) {
  request.respond({
    status,
    contentType: "application/json",
    headers: {
      "access-control-allow-origin": FRONTEND_URL,
      "access-control-allow-credentials": "true",
      "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
      "access-control-allow-headers": "content-type",
    },
    body: JSON.stringify(body),
  });
}

async function waitForText(page, expected, seenRequests = []) {
  try {
    await page.waitForFunction(
      (text) => document.body.textContent?.includes(text),
      { timeout: 5_000 },
      expected,
    );
  } catch (error) {
    const body = await page.$eval("body", (element) => element.innerText);
    throw new Error(
      `Missing UI message: ${expected}\nBackend requests: ${seenRequests.join(", ")}\n${body}`,
      { cause: error },
    );
  }
}

async function waitForServer(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // Server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Frontend did not start: ${url}`);
}

function requirePort(name) {
  const value = process.env[name];
  if (!value || !/^\d+$/.test(value)) {
    throw new Error(`${name} must be set to a valid port in .env.local`);
  }
  return Number(value);
}

function requireOrigin(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} must be set in .env.local`);
  }
  return new URL(value).origin;
}
