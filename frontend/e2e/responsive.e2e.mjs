/**
 * Responsive layout check (render-level, not just type-level).
 *
 * Boots the production build and, at several real device widths, asserts that:
 *   1. the page does not scroll horizontally (document scrollWidth <= viewport width),
 *      which is the concrete symptom of "fixed size layout" overflow;
 *   2. no element sticks out past the right edge of the viewport;
 *   3. the nav links remain reachable (not zero-sized / collapsed on top of each other).
 *
 * Intentionally wide, horizontally-scrollable regions (the option-chain and stocks
 * grids) are allowed to overflow INTERNALLY — that is by design — so we measure the
 * document, and skip elements inside an `overflow-x: auto` ancestor.
 *
 * Run:  CHROMIUM_PATH=/usr/bin/chromium node --test e2e/responsive.e2e.mjs
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { after, before, describe, it } from "node:test";

import puppeteer from "puppeteer-core";

const PORT = process.env.RESPONSIVE_PORT || "13998";
const BASE = `http://127.0.0.1:${PORT}`;

const VIEWPORTS = [
  { name: "iPhone SE (375x667)", width: 375, height: 667 },
  { name: "iPhone 14 Pro (393x852)", width: 393, height: 852 },
  { name: "Pixel 7 (412x915)", width: 412, height: 915 },
  { name: "iPad mini (768x1024)", width: 768, height: 1024 },
];

const PAGES = ["/", "/monitor", "/option-chain", "/stocks", "/login"];

let server;
let browser;

async function waitForServer(url, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status === 404) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`server did not start at ${url}`);
}

before(async () => {
  // Same-origin build: no backend needed for a layout check (API calls simply fail,
  // which is fine — we are measuring geometry, not data).
  server = spawn("node", [".next/standalone/server.js"], {
    cwd: process.cwd(),
    env: { ...process.env, PORT, HOSTNAME: "127.0.0.1", NEXT_PUBLIC_BACKEND_URL: "" },
    stdio: "ignore",
  });
  await waitForServer(`${BASE}/login`);
  browser = await puppeteer.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
});

after(async () => {
  if (browser) await browser.close();
  if (server) server.kill("SIGTERM");
});

describe("responsive layout", () => {
  for (const vp of VIEWPORTS) {
    for (const path of PAGES) {
      it(`${path} has no horizontal overflow at ${vp.name}`, async () => {
        const page = await browser.newPage();
        try {
          await page.setViewport({ width: vp.width, height: vp.height, deviceScaleFactor: 2 });
          await page.goto(`${BASE}${path}`, { waitUntil: "networkidle2", timeout: 30_000 });
          // let client components settle
          await new Promise((r) => setTimeout(r, 400));

          const result = await page.evaluate(() => {
            const de = document.documentElement;
            const vw = de.clientWidth;

            const inScroller = (el) => {
              for (let p = el.parentElement; p; p = p.parentElement) {
                const ox = getComputedStyle(p).overflowX;
                if (ox === "auto" || ox === "scroll") return true;
              }
              return false;
            };

            const offenders = [];
            for (const el of document.body.querySelectorAll("*")) {
              const r = el.getBoundingClientRect();
              if (r.width === 0 && r.height === 0) continue;
              if (inScroller(el)) continue; // by-design horizontal scroll region
              if (r.right > vw + 1) {
                offenders.push({
                  tag: el.tagName.toLowerCase(),
                  cls: (el.className || "").toString().slice(0, 70),
                  right: Math.round(r.right),
                });
              }
            }
            return {
              viewportWidth: vw,
              docScrollWidth: de.scrollWidth,
              offenders: offenders.slice(0, 5),
            };
          });

          assert.ok(
            result.docScrollWidth <= result.viewportWidth + 1,
            `document scrolls horizontally: scrollWidth=${result.docScrollWidth} > viewport=${result.viewportWidth}`,
          );
          assert.deepEqual(
            result.offenders,
            [],
            `elements overflow the right edge: ${JSON.stringify(result.offenders)}`,
          );
        } finally {
          await page.close();
        }
      });
    }
  }

  it("nav links stay visible and tappable at 375px", async () => {
    const page = await browser.newPage();
    try {
      await page.setViewport({ width: 375, height: 667 });
      await page.goto(`${BASE}/`, { waitUntil: "networkidle2", timeout: 30_000 });
      const links = await page.evaluate(() =>
        [...document.querySelectorAll("nav a")].map((a) => {
          const r = a.getBoundingClientRect();
          return { text: a.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height) };
        }),
      );
      assert.ok(links.length >= 5, `expected brand + 4 nav links, got ${links.length}`);
      for (const l of links) {
        assert.ok(l.w > 0 && l.h >= 20, `nav link "${l.text}" collapsed: ${l.w}x${l.h}`);
      }
    } finally {
      await page.close();
    }
  });

  it("monitor scroll containers have room for multiple rows on a desktop viewport", async () => {
    const page = await browser.newPage();
    try {
      // 1440x900 is the pinned-dashboard case (lg): every scroll region must still be
      // tall enough to show more than a single table row, which was the complaint about
      // the download-history panel.
      await page.setViewport({ width: 1440, height: 900 });
      await page.goto(`${BASE}/monitor`, { waitUntil: "networkidle2", timeout: 30_000 });
      await new Promise((r) => setTimeout(r, 600));

      const heights = await page.evaluate(() =>
        [...document.querySelectorAll("section")].map((s) => {
          const title = s.querySelector("h2")?.textContent?.trim() ?? "?";
          const scroller = s.querySelector(".overflow-auto");
          return {
            title,
            panel: Math.round(s.getBoundingClientRect().height),
            scroller: scroller ? Math.round(scroller.getBoundingClientRect().height) : null,
          };
        }),
      );
      // A table row is ~22px at this density; 60px guarantees header + 2 rows.
      for (const h of heights) {
        if (h.scroller === null) continue;
        assert.ok(
          h.scroller >= 60,
          `panel "${h.title}" scroll area is only ${h.scroller}px tall (panel ${h.panel}px)`,
        );
      }
    } finally {
      await page.close();
    }
  });
});
