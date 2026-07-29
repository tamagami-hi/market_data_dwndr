import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { after, before, describe, it } from "node:test";

import puppeteer from "puppeteer";

const PORT = process.env.OPTION_LAYOUT_PORT || "13994";
const BASE = `http://127.0.0.1:${PORT}`;
const STRIKES = Array.from({ length: 101 }, (_, index) => 19_500 + index * 50);

let server;
let browser;

async function waitForServer(url, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`server did not start at ${url}`);
}

function block(offset) {
  const values = (scale = 1) => STRIKES.map((_, index) => (offset + index + 1) * scale);
  return {
    oi: values(1_000),
    change_in_oi: values(100),
    volume: values(500),
    iv: values(0.1),
    delta: values(0.001),
    gamma: values(0.000001),
    theta: values(-0.001),
    vega: values(0.001),
    rho: values(0.001),
    bid: values(0.5),
    ask: values(0.51),
    ltp: values(0.505),
    change: values(0.01),
  };
}

async function installMockSocket(page) {
  const calls = block(0);
  const puts = block(100);
  await page.evaluateOnNewDocument((fixture) => {
    class MockWebSocket {
      static OPEN = 1;
      readyState = 0;
      onopen = null;
      onmessage = null;
      onerror = null;
      onclose = null;

      constructor(url) {
        this.url = String(url);
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
          if (this.url.endsWith("/ws/market-data")) {
            this.emit({
              type: "MarketHeader",
              payload: fixture.header,
              meta: { pipeline_ms: 30, greeks_ms: 22, stocks_ms: 5 },
            });
            this.emit({
              type: "OptionGrid",
              payload: fixture.grid,
              meta: { pipeline_ms: 30, greeks_ms: 22, stocks_ms: 5 },
            });
          } else if (this.url.endsWith("/ws/session")) {
            this.emit({ type: "SessionStatus", payload: { phase: "connected" } });
          }
        }, 0);
      }

      emit(envelope) {
        this.onmessage?.({ data: JSON.stringify(envelope) });
      }

      close() {
        this.readyState = 3;
      }
    }
    window.WebSocket = MockWebSocket;
  }, {
    header: {
      underlying: "NIFTY",
      expiry: "2026-08-04",
      spot: 22_010,
      atm: 22_000,
      vix: 12.1,
      risk_free_rate: 0.0533,
      timestamp: 1,
      sequence: 42,
    },
    grid: {
      underlying: "NIFTY",
      expiry: "2026-08-04",
      strikes: STRIKES,
      calls,
      puts,
      market_atm: 22_000,
      max_pain: 21_950,
      spot_atm: 22_000,
      spot: 22_010,
      vix: 12.1,
    },
  });
}

async function openOptionPage(width, height) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  await installMockSocket(page);
  await page.goto(`${BASE}/option-chain`, { waitUntil: "networkidle2" });
  await page.waitForSelector("[data-option-table-frame]");
  return page;
}

before(async () => {
  server = spawn("node", ["node_modules/next/dist/bin/next", "start", "-p", PORT], {
    cwd: process.cwd(),
    env: { ...process.env, PORT, HOSTNAME: "127.0.0.1", NEXT_PUBLIC_BACKEND_URL: "" },
    stdio: "ignore",
  });
  await waitForServer(`${BASE}/`);
  browser = await puppeteer.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
});

after(async () => {
  await browser?.close();
  server?.kill("SIGTERM");
});

describe("option-chain dense desktop layout", () => {
  it("keeps every supplied strike reachable above and below ATM", async () => {
    const page = await openOptionPage(1440, 900);
    try {
      const top = await page.evaluate(() => {
        const frame = document.querySelector("[data-option-table-frame]");
        const strikes = [...frame.querySelectorAll("tbody td.sticky span:first-child")]
          .map((element) => Number(element.textContent.replaceAll(",", "")));
        return {
          first: Math.min(...strikes),
          scrollHeight: frame.scrollHeight,
          clientHeight: frame.clientHeight,
        };
      });
      assert.equal(top.first, 19_500);
      assert.ok(top.scrollHeight > top.clientHeight);

      const bottom = await page.evaluate(async () => {
        const frame = document.querySelector("[data-option-table-frame]");
        frame.scrollTop = frame.scrollHeight;
        await new Promise(requestAnimationFrame);
        const strikes = [...frame.querySelectorAll("tbody td.sticky span:first-child")]
          .map((element) => Number(element.textContent.replaceAll(",", "")));
        return Math.max(...strikes);
      });
      assert.equal(bottom, 24_500);

      const aroundAtm = await page.evaluate(async () => {
        const frame = document.querySelector("[data-option-table-frame]");
        frame.scrollTop = (frame.scrollHeight - frame.clientHeight) / 2;
        await new Promise(requestAnimationFrame);
        const strikes = [...frame.querySelectorAll("tbody td.sticky span:first-child")]
          .map((element) => Number(element.textContent.replaceAll(",", "")));
        return { min: Math.min(...strikes), max: Math.max(...strikes) };
      });
      assert.ok(aroundAtm.min < 22_000 && aroundAtm.max > 22_000, JSON.stringify(aroundAtm));
    } finally {
      await page.close();
    }
  });

  for (const [width, height] of [[1280, 800], [1440, 900], [1920, 1080], [3200, 1800]]) {
    it(`uses readable fixed geometry without horizontal scrolling at ${width}px`, async () => {
      const page = await openOptionPage(width, height);
      try {
        const geometry = await page.evaluate(() => {
          const frame = document.querySelector("[data-option-table-frame]");
          const frameRect = frame.getBoundingClientRect();
          const cells = [...frame.querySelectorAll("tbody td:not(.sticky)")]
            .filter((element) => element.textContent.trim());
          const style = getComputedStyle(cells[0]);
          return {
            documentWidth: document.documentElement.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            frameWidth: frameRect.width,
            frameCenter: (frameRect.left + frameRect.right) / 2,
            frameClientWidth: frame.clientWidth,
            frameScrollWidth: frame.scrollWidth,
            fontSize: Number.parseFloat(style.fontSize),
            horizontalPadding:
              Number.parseFloat(style.paddingLeft) + Number.parseFloat(style.paddingRight),
            clippedCells: cells.filter(
              (element) => element.scrollWidth > element.clientWidth + 1,
            ).length,
            clippedSample: cells
              .filter((element) => element.scrollWidth > element.clientWidth + 1)
              .slice(0, 4)
              .map((element) => ({
                text: element.textContent.trim(),
                column: [...element.parentElement.children].indexOf(element),
                width: element.clientWidth,
                scrollWidth: element.scrollWidth,
              })),
          };
        });
        assert.ok(geometry.documentWidth <= geometry.viewportWidth + 1, JSON.stringify(geometry));
        assert.ok(geometry.frameScrollWidth <= geometry.frameClientWidth + 1, JSON.stringify(geometry));
        assert.ok(geometry.frameWidth <= 1_600, JSON.stringify(geometry));
        assert.ok(
          Math.abs(geometry.frameCenter - geometry.documentWidth / 2) <= 1,
          JSON.stringify(geometry),
        );
        assert.ok(geometry.fontSize >= 11, JSON.stringify(geometry));
        assert.ok(geometry.horizontalPadding <= 2, JSON.stringify(geometry));
        assert.equal(geometry.clippedCells, 0, JSON.stringify(geometry));
      } finally {
        await page.close();
      }
    });
  }
});
