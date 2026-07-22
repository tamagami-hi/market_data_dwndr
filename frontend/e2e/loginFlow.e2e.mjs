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

test("unlocks the operations console without persisting the operator token", async () => {
  const page = await browser.newPage();
  const operatorToken = "operator-test-token-with-32-characters";
  let isUnlocked = false;
  let submittedToken = null;
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== BACKEND_ORIGIN) {
      request.continue();
      return;
    }
    if (request.method() === "OPTIONS") {
      respondJson(request, {});
      return;
    }
    if (url.pathname === "/api/operator/status") {
      respondJson(request, { unlocked: isUnlocked });
      return;
    }
    if (url.pathname === "/api/operator/unlock" && request.method() === "POST") {
      submittedToken = JSON.parse(request.postData()).token;
      isUnlocked = true;
      respondJson(request, { unlocked: true, expires_at: Date.now() + 60_000 });
      return;
    }
    request.abort();
  });

  await page.goto(FRONTEND_URL, { waitUntil: "networkidle0" });
  await waitForText(page, "Operator unlock required");
  await page.type('input[aria-label="Operator token"]', operatorToken);
  await clickButton(page, "Unlock console");
  await waitForText(page, "Zerodha Kite market-data downloader");

  const persisted = await page.evaluate(() => ({
    local: Object.values(localStorage),
    session: Object.values(sessionStorage),
    body: document.body.textContent,
  }));
  assert.equal(submittedToken, operatorToken);
  assert.equal(persisted.local.includes(operatorToken), false);
  assert.equal(persisted.session.includes(operatorToken), false);
  assert.equal(persisted.body.includes(operatorToken), false);
  await page.close();
});

test("completes shared-check fallback, TOTP, rate, and successful login messaging", async () => {
  const page = await browser.newPage();
  let isAuthenticated = false;
  const seenRequests = await mockBackend(page, () => isAuthenticated, () => {
    isAuthenticated = true;
  });

  await page.goto(`${FRONTEND_URL}/login`, { waitUntil: "networkidle0" });
  await waitForText(page, "Shared token source", seenRequests);
  await clickButton(page, "Start login");
  await waitForText(page, "Environment credentials accepted. Enter your TOTP.", seenRequests);

  await page.type('input[placeholder="123456"]', "654321");
  await clickButton(page, "Verify TOTP");
  await waitForText(page, "TOTP verified and the access token was issued.");

  await page.type('input[placeholder="0.0691"]', "0.0691");
  await clickButton(page, "Complete login");
  await waitForText(page, "Login cycle completed successfully.");
  await waitForText(page, "Session 2026-07-22 · 10-year yield 0.0691");

  assert.equal(isAuthenticated, true);
  await page.close();
});

test("explains a third-day yield block and enables capture after an update", async () => {
  const page = await browser.newPage();
  let isRateUpdateRequired = true;
  await mockYieldUpdate(page, () => isRateUpdateRequired, () => {
    isRateUpdateRequired = false;
  });

  await page.goto(`${FRONTEND_URL}/login`, { waitUntil: "networkidle0" });
  await waitForText(page, "10-year bond yield update required");
  await waitForText(page, "no TOTP or new login is needed");
  await page.type('input[placeholder="0.0691"]', "0.0665");
  await clickButton(page, "Update yield and enable capture");
  await waitForText(page, "Yield updated. Automatic capture is now ready.");

  assert.equal(isRateUpdateRequired, false);
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
    if (url.origin === BACKEND_ORIGIN && url.pathname === "/api/operator/status") {
      respondJson(request, { unlocked: true });
      return;
    }
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

async function mockBackend(page, getAuthenticated, setAuthenticated) {
  const seenRequests = [];
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== BACKEND_ORIGIN) {
      request.continue();
      return;
    }
    seenRequests.push(`${request.method()} ${url.pathname}`);

    if (url.pathname === "/api/operator/status") {
      respondJson(request, { unlocked: true });
      return;
    }

    const progress = {
      attempt_id: "e2e-attempt",
      trading_date: "2026-07-22",
      expires_at: Date.now() + 180_000,
      method: "local_credentials",
    };
    if (url.pathname === "/api/auth/status") {
      respondJson(request, {
        configured: true,
        authenticated: getAuthenticated(),
        trading_date: "2026-07-22",
        market_phase: "open",
        credentials_present: true,
        external_token_source_configured: true,
      });
      return;
    }
    if (url.pathname === "/api/auth/login-url") {
      respondJson(request, { login_url: "https://kite.example/login" });
      return;
    }
    if (url.pathname === "/api/auth/login/start") {
      respondJson(request, { ...progress, step: "awaiting_totp" }, 202);
      return;
    }
    if (url.pathname.endsWith("/totp")) {
      respondJson(request, { ...progress, step: "awaiting_risk_free_rate" });
      return;
    }
    if (url.pathname.endsWith("/complete")) {
      setAuthenticated();
      respondJson(request, {
        authenticated: true,
        trading_date: "2026-07-22",
        risk_free_rate: 0.0691,
      });
      return;
    }
    request.abort();
  });
  return seenRequests;
}

async function mockYieldUpdate(page, getRequired, clearRequired) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== BACKEND_ORIGIN) {
      request.continue();
      return;
    }
    if (url.pathname === "/api/operator/status") {
      respondJson(request, { unlocked: true });
      return;
    }
    if (request.method() === "OPTIONS") {
      respondJson(request, {});
      return;
    }
    if (url.pathname === "/api/auth/status") {
      respondJson(request, {
        configured: true,
        authenticated: true,
        trading_date: "2026-07-22",
        market_phase: "PRE_OPEN",
        credentials_present: true,
        external_token_source_configured: true,
        risk_free_rate: 0.065,
        risk_free_rate_as_of: "2026-07-20",
        rate_update_required: getRequired(),
        capture_ready: !getRequired(),
        automation: { phase: "capture_window" },
      });
      return;
    }
    if (url.pathname === "/api/auth/login-url") {
      respondJson(request, { login_url: "https://kite.example/login" });
      return;
    }
    if (url.pathname === "/api/auth/risk-free-rate" && request.method() === "PUT") {
      clearRequired();
      respondJson(request, {
        authenticated: true,
        trading_date: "2026-07-22",
        risk_free_rate: 0.0665,
        risk_free_rate_as_of: "2026-07-22",
        rate_update_required: false,
        capture_ready: true,
      });
      return;
    }
    request.abort();
  });
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

async function clickButton(page, label) {
  const buttons = await page.$$("button");
  for (const button of buttons) {
    const text = await button.evaluate((element) => element.textContent?.trim());
    if (text === label) {
      await button.click();
      return;
    }
  }
  throw new Error(`Button not found: ${label}`);
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
