/**
 * Layout audit screenshot + geometry capture.
 *
 * Usage: node shot.mjs <label> [url]
 *   node shot.mjs v0.1.32
 *   node shot.mjs local http://127.0.0.1:13990/monitor
 *
 * Writes <label>-desktop.png, <label>-mobile.png and <label>.json (panel geometry)
 * into this directory. The JSON is what the comparison actually relies on — pixel
 * diffs move with colour changes, whereas panel boxes describe the layout only.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const label = process.argv[2];
const url = process.argv[3] || "http://tickvault.beonedge.internal/monitor";
if (!label) {
  console.error("usage: node shot.mjs <label> [url]");
  process.exit(1);
}

const VIEWPORTS = [
  { name: "desktop", width: 1600, height: 1000 },
  { name: "mobile", width: 390, height: 844 },
];

const browser = await puppeteer.launch({
  executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

const report = { label, url, capturedAt: new Date().toISOString(), viewports: {} };

for (const vp of VIEWPORTS) {
  const page = await browser.newPage();
  await page.setViewport({ width: vp.width, height: vp.height, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: "networkidle2", timeout: 45_000 });
  // Let client components settle and one telemetry tick land.
  await new Promise((r) => setTimeout(r, 2500));

  const geometry = await page.evaluate(() => {
    const round = (n) => Math.round(n);
    const panels = [...document.querySelectorAll("section")]
      .map((section) => {
        const heading = section.querySelector("h1,h2,h3");
        const rect = section.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return null;
        return {
          title: heading?.textContent?.trim().slice(0, 40) ?? "(untitled)",
          top: round(rect.top + window.scrollY),
          left: round(rect.left),
          width: round(rect.width),
          height: round(rect.height),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.top - b.top || a.left - b.left);

    const main = document.querySelector("main");
    return {
      documentHeight: round(document.documentElement.scrollHeight),
      mainWidth: main ? round(main.getBoundingClientRect().width) : null,
      panelCount: panels.length,
      panels,
    };
  });

  report.viewports[vp.name] = geometry;
  mkdirSync(HERE, { recursive: true });
  await page.screenshot({
    path: resolve(HERE, `${label}-${vp.name}.png`),
    fullPage: true,
  });
  await page.close();
  console.log(`  ${label}/${vp.name}: ${geometry.panelCount} panels, doc height ${geometry.documentHeight}px`);
}

writeFileSync(resolve(HERE, `${label}.json`), `${JSON.stringify(report, null, 2)}\n`);
await browser.close();
console.log(`  wrote ${label}.json`);
