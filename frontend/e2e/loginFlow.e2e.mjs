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
  await waitForText(page, "Session 2026-07-22 · rate 0.0691");

  assert.equal(isAuthenticated, true);
  await page.close();
});

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

function respondJson(request, body, status = 200) {
  request.respond({
    status,
    contentType: "application/json",
    headers: {
      "access-control-allow-origin": FRONTEND_URL,
      "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
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
