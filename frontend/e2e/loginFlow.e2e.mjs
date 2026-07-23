import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test, { after, before } from "node:test";

import puppeteer from "puppeteer-core";

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
  await waitForText(page, "Initializing downloader", seenRequests);
  await waitForText(page, "Downloader is running", seenRequests);
  await waitForText(page, "Token fetch and validation", seenRequests);
  await waitForText(page, "100%", seenRequests);

  assert.ok(seenRequests.includes("GET /api/auth/status"));
  await page.close();
});

test("expands a stock row to show all five market-depth levels", async () => {
  const page = await browser.newPage();
  await installMockWebSocket(page);
  await mockStockDepth(page);
  await page.goto(`${FRONTEND_URL}/stocks`, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => Boolean(window.__stockSocket));

  await page.evaluate((message) => {
    window.__stockSocket.onmessage?.({ data: JSON.stringify(message) });
  }, stockBoardMessage());

  await waitForText(page, "RELIANCE");
  const toggle = await page.$('button[aria-label="Show L5 depth for RELIANCE"]');
  assert.ok(toggle, "stock depth toggle should be accessible by name");
  await toggle.focus();
  await page.keyboard.press("Enter");

  await waitForText(page, "Spot order book");
  await waitForText(page, "Current future order book");
  await waitForText(page, "2,459.70");
  await waitForText(page, "2,460.30");
  const depthRowCounts = await page.$$eval(
    '[aria-label="RELIANCE L5 market depth"] tbody',
    (bodies) => bodies.map((body) => body.querySelectorAll("tr").length),
  );
  assert.deepEqual(depthRowCounts, [5, 5, 5, 5]);
  assert.equal(await toggle.evaluate((element) => element.getAttribute("aria-expanded")), "true");
  await page.close();
});

async function installMockWebSocket(page) {
  await page.evaluateOnNewDocument(() => {
    class MockWebSocket {
      constructor() {
        window.__stockSocket = this;
        setTimeout(() => this.onopen?.({}), 0);
      }

      close() {}
    }
    window.WebSocket = MockWebSocket;
  });
}

function stockBoardMessage() {
  return {
    type: "StockBoard",
    payload: {
      timestamp: Date.now(),
      stocks: [
        {
          tradingsymbol: "RELIANCE",
          name: "RELIANCE",
          spot_ltp: 2455.5,
          futures: [
            { expiry: "2026-07-30", ltp: 2460, oi: 8000 },
            { expiry: "2026-08-27", ltp: 2475, oi: 6000 },
            { expiry: "2026-09-24", ltp: 2488, oi: 4000 },
          ],
          live_spread: 15,
          daily_spread: 13,
        },
      ],
    },
  };
}

function depthLevels() {
  return Array.from({ length: 5 }, (_, index) => ({
    level: index + 1,
    bid_price: 2459.9 - index * 0.05,
    bid_qty: 100 + index,
    bid_orders: index + 1,
    ask_price: 2460.1 + index * 0.05,
    ask_qty: 200 + index,
    ask_orders: index + 2,
  }));
}

async function mockStockDepth(page) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== BACKEND_ORIGIN || url.pathname !== "/api/capture/stocks/RELIANCE/depth") {
      request.continue();
      return;
    }
    const depth = depthLevels();
    respondJson(request, {
      tradingsymbol: "RELIANCE",
      name: "RELIANCE",
      spot_depth: depth,
      futures: [
        { label: "Current future", expiry: "2026-07-30", depth },
        { label: "Mid future", expiry: "2026-08-27", depth },
        { label: "Far future", expiry: "2026-09-24", depth },
      ],
    });
  });
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
        static_ip_configured: true,
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
