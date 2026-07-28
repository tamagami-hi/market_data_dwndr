import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import test, { after, before } from "node:test";

import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import puppeteer from "puppeteer";

const PORT = process.env.VISUAL_PORT || "13996";
const BASE = `http://127.0.0.1:${PORT}`;
const CASES = [
  ["/", "home-desktop", 1440, 900],
  ["/monitor", "monitor-desktop", 1440, 900],
  ["/monitor", "monitor-mobile", 393, 852],
  ["/option-chain", "option-chain-mobile", 393, 852],
  ["/stocks", "stocks-desktop", 1440, 900],
  ["/login", "downloader-mobile", 393, 852],
];
const isUpdating = process.env.UPDATE_VISUAL_BASELINES === "1";
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
  await mkdir("e2e/baselines", { recursive: true });
});

after(async () => {
  await browser?.close();
  server?.kill("SIGTERM");
});

for (const [route, name, width, height] of CASES) {
  test(`${name} matches its screenshot baseline`, async () => {
    const page = await browser.newPage();
    await page.setViewport({ width, height, deviceScaleFactor: 1 });
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle2" });
    const screenshot = Buffer.from(await page.screenshot({ fullPage: true }));
    await page.close();

    const baselinePath = path.join("e2e", "baselines", `${name}.png`);
    if (isUpdating) {
      await writeFile(baselinePath, screenshot);
      return;
    }

    const expected = PNG.sync.read(await readFile(baselinePath));
    const actual = PNG.sync.read(screenshot);
    assert.equal(actual.width, expected.width, `${name} width changed`);
    assert.equal(actual.height, expected.height, `${name} height changed`);
    const diff = new PNG({ width: expected.width, height: expected.height });
    const changedPixels = pixelmatch(
      expected.data,
      actual.data,
      diff.data,
      expected.width,
      expected.height,
      { threshold: 0.12 },
    );
    const changedRatio = changedPixels / (expected.width * expected.height);
    if (changedRatio > 0.005) {
      await mkdir("e2e/diffs", { recursive: true });
      await writeFile(path.join("e2e", "diffs", `${name}.png`), PNG.sync.write(diff));
    }
    assert.ok(changedRatio <= 0.005, `${name} changed by ${(changedRatio * 100).toFixed(2)}%`);
  });
}
