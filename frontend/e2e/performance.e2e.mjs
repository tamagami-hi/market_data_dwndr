import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test, { after, before } from "node:test";

import puppeteer from "puppeteer";

const PORT = process.env.PERFORMANCE_PORT || "13997";
const BASE = `http://127.0.0.1:${PORT}`;
let server;
let browser;

before(async () => {
  server = spawn("node", ["node_modules/next/dist/bin/next", "start", "-p", PORT], {
    cwd: process.cwd(),
    env: { ...process.env, HOSTNAME: "127.0.0.1", NEXT_PUBLIC_BACKEND_URL: "" },
    stdio: "ignore",
  });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      if ((await fetch(BASE)).ok) break;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await puppeteer.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
});

after(async () => {
  await browser?.close();
  server?.kill("SIGTERM");
});

test("mocked 1 Hz option snapshots stay below the 50ms p95 virtualization gate", async () => {
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  await page.evaluateOnNewDocument(() => {
    class MockWebSocket {
      constructor(url) {
        if (String(url).includes("market-data")) window.__marketSocket = this;
        setTimeout(() => this.onopen?.({}), 0);
      }
      close() {}
    }
    window.WebSocket = MockWebSocket;
  });
  await page.goto(`${BASE}/option-chain`, { waitUntil: "networkidle2" });
  await page.waitForFunction(() => Boolean(window.__marketSocket));

  const result = await page.evaluate(async () => {
    const keys = ["oi", "change_in_oi", "volume", "iv", "delta", "gamma", "theta", "vega", "rho", "bid", "ask", "ltp", "change"];
    const strikes = Array.from({ length: 101 }, (_, index) => 19_500 + index * 50);
    const block = (offset) => Object.fromEntries(
      keys.map((key, keyIndex) => [key, strikes.map((_, row) => offset + keyIndex + row / 100)]),
    );
    const payload = {
      underlying: "NIFTY",
      expiry: "2026-07-30",
      strikes,
      calls: block(0),
      puts: block(100),
      market_atm: 22_000,
      max_pain: 22_050,
      spot_atm: 22_000,
      spot: 22_010,
      vix: 12,
    };
    const send = () => window.__marketSocket.onmessage?.({
      data: JSON.stringify({ type: "OptionGrid", payload }),
    });
    send();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    const longTasks = [];
    const observer = "PerformanceObserver" in window
      ? new PerformanceObserver((list) => longTasks.push(...list.getEntries().map((entry) => entry.duration)))
      : null;
    try {
      observer?.observe({ entryTypes: ["longtask"] });
    } catch {}

    const durations = [];
    for (let index = 0; index < 15; index += 1) {
      payload.calls.ltp[50] += 0.05;
      const startedAt = performance.now();
      send();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      durations.push(performance.now() - startedAt);
    }
    observer?.disconnect();
    const sorted = [...durations].sort((left, right) => left - right);
    return {
      p95: sorted[Math.ceil(sorted.length * 0.95) - 1],
      repeatedLongTasks: longTasks.filter((duration) => duration > 50).length,
    };
  });

  assert.ok(result.p95 < 50, `option-grid p95 ${result.p95.toFixed(1)}ms exceeds 50ms`);
  assert.ok(result.repeatedLongTasks < 2, `${result.repeatedLongTasks} repeated long tasks`);
  await page.close();
});
