import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { after, before, describe, it } from "node:test";

import AxePuppeteer from "@axe-core/puppeteer";
import puppeteer from "puppeteer";

const PORT = process.env.RESPONSIVE_PORT || "13998";
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWPORTS = [
  [375, 667],
  [393, 852],
  [412, 915],
  [768, 1024],
  [1024, 768],
  [1440, 900],
  [1600, 1000],
  [1920, 1080],
  [2400, 1350],
  [3200, 1800],
];
const ROUTES = ["/", "/monitor", "/option-chain", "/stocks", "/login"];

let server;
let browser;

async function waitForServer(url, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok || response.status === 404) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`server did not start at ${url}`);
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

async function openPage(path, width, height) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: width < 800 ? 2 : 1 });
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle2", timeout: 30_000 });
  return page;
}

async function openMonitorWithTelemetry(width, height) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(() => {
    window.__testSockets = {};
    class MockWebSocket {
      constructor(url) {
        this.url = String(url);
        window.__testSockets[this.url] = this;
        setTimeout(() => this.onopen?.({}), 0);
      }

      close() {}
    }
    window.WebSocket = MockWebSocket;
  });
  await page.goto(`${BASE}/monitor`, { waitUntil: "networkidle2", timeout: 30_000 });
  await page.waitForFunction(() =>
    Object.keys(window.__testSockets ?? {}).some((url) =>
      url.endsWith("/ws/capture-status"),
    ),
  );
  await page.evaluate(() => {
    const socketEntry = Object.entries(window.__testSockets).find(([url]) =>
      url.endsWith("/ws/capture-status"),
    );
    const socket = socketEntry?.[1];
    const underlyings = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"];
    socket?.onmessage?.({
      data: JSON.stringify({
        type: "CaptureStatus",
        payload: {
          per_underlying: underlyings.map((underlying, index) => ({
            underlying,
            connected: index !== 3,
            last_tick_ms: Date.now(),
            frames_written: 23_000 + index,
            frames_expected: 23_520,
            frame_loss_pct: 0.1,
            session_frames_expected: 10_000,
            session_loss_pct: 0.02,
            day_complete_pct: 76.4,
            file_bytes: 123_456_789,
            avg_bytes_per_frame: 5_368,
            projected_eod_bytes: 165_432_100,
            heartbeat_ok: index !== 3,
            heartbeat_age_ms: 200,
            data_fresh: index !== 3,
            unmatched: 0,
            applied: 1_000,
            writer_pending: 0,
          })),
          global: { fps: 5 },
        },
      }),
    });
  });
  await page.waitForFunction(() =>
    document.querySelectorAll(
      '.monitor-health-table [role="img"][aria-label*="connection"]',
    ).length === 5,
  );
  return page;
}

describe("responsive workstation", () => {
  for (const [width, height] of VIEWPORTS) {
    for (const path of ROUTES) {
      it(`${path} has no document overflow at ${width}px`, async () => {
        const page = await openPage(path, width, height);
        try {
          const geometry = await page.evaluate(() => ({
            viewport: document.documentElement.clientWidth,
            document: document.documentElement.scrollWidth,
          }));
          assert.ok(
            geometry.document <= geometry.viewport + 1,
            `${path} scrollWidth ${geometry.document} exceeds ${geometry.viewport}`,
          );
        } finally {
          await page.close();
        }
      });
    }
  }

  it("mobile navigation has four equal 44px route targets", async () => {
    const page = await openPage("/", 375, 667);
    try {
      const targets = await page.evaluate(() =>
        [...document.querySelectorAll('nav a[href]:not([href="/"])')]
          .filter((link) => link.getBoundingClientRect().width > 0)
          .map((link) => {
            const rect = link.getBoundingClientRect();
            return { label: link.getAttribute("aria-label"), width: rect.width, height: rect.height };
          }),
      );
      assert.equal(targets.length, 4);
      targets.forEach((target) => assert.ok(target.height >= 44, `${target.label} is ${target.height}px tall`));
      assert.ok(Math.max(...targets.map((target) => target.width)) - Math.min(...targets.map((target) => target.width)) <= 1);
    } finally {
      await page.close();
    }
  });

  it("mobile notification history stays inside the viewport below navigation", async () => {
    const page = await openPage("/", 375, 812);
    try {
      const trigger = await page.$('button[aria-controls="notification-history"]');
      assert.ok(trigger, "notification trigger is missing");
      await trigger.click();
      await page.waitForSelector('#notification-history[role="dialog"]');
      const geometry = await page.evaluate(() => {
        const panel = document.querySelector("#notification-history");
        const navigation = document.querySelector("nav");
        const panelRect = panel?.getBoundingClientRect();
        const navRect = navigation?.getBoundingClientRect();
        return {
          triggerCount: document.querySelectorAll(
            'button[aria-controls="notification-history"]',
          ).length,
          left: panelRect?.left,
          right: panelRect?.right,
          top: panelRect?.top,
          navBottom: navRect?.bottom,
          viewport: document.documentElement.clientWidth,
        };
      });
      assert.equal(geometry.triggerCount, 1);
      assert.ok(geometry.left >= 0, JSON.stringify(geometry));
      assert.ok(geometry.right <= geometry.viewport, JSON.stringify(geometry));
      assert.ok(geometry.top >= geometry.navBottom, JSON.stringify(geometry));
    } finally {
      await page.close();
    }
  });

  it("monitor REST failures use timestamped notification history instead of inline alerts", async () => {
    const page = await browser.newPage();
    try {
      await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
      await page.evaluateOnNewDocument(() => window.localStorage.clear());
      await page.goto(`${BASE}/monitor`, {
        waitUntil: "networkidle2",
        timeout: 30_000,
      });
      await page.waitForFunction(() =>
        document.querySelector(
          'button[aria-label^="Notifications,"][aria-label$="unread"]',
        ),
      );
      const hasInlineRestAlert = await page.evaluate(() => {
        const alerts = document.querySelector('section[aria-label="Operational alerts"]');
        return alerts?.textContent?.includes("REST refresh failed") ?? false;
      });
      assert.equal(hasInlineRestAlert, false);

      await page.click('button[aria-controls="notification-history"]');
      await page.waitForSelector('#notification-history[role="dialog"]');
      const history = await page.evaluate(() => {
        const panel = document.querySelector("#notification-history");
        return {
          text: panel?.textContent,
          timestamps: panel?.querySelectorAll("time").length,
        };
      });
      assert.match(history.text ?? "", /Monitor REST refresh failed/);
      assert.match(history.text ?? "", /Today/);
      assert.doesNotMatch(history.text ?? "", /newest first/i);
      assert.ok((history.timestamps ?? 0) >= 1);
    } finally {
      await page.close();
    }
  });

  it("skip link moves focus to main content", async () => {
    const page = await openPage("/", 1024, 768);
    try {
      await page.keyboard.press("Tab");
      assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), "Skip to main content");
      await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(() => document.activeElement?.id), "main-content");
    } finally {
      await page.close();
    }
  });

  it("monitor pairs panels into three symmetric aligned rows", async () => {
    const page = await openPage("/monitor", 1440, 900);
    try {
      const boxes = await page.evaluate(() => {
        const find = (title) => {
          const heading = [...document.querySelectorAll("h2")].find((item) => item.textContent?.trim() === title);
          const rect = heading?.closest(".panel")?.getBoundingClientRect();
          return rect ? {
            top: Math.round(rect.top),
            bottom: Math.round(rect.bottom),
            left: Math.round(rect.left),
            width: Math.round(rect.width),
          } : null;
        };
        return {
          health: find("Per-underlying health"),
          loss: find("Data-loss diagnostics"),
          sessions: find("Session history"),
          storage: find("Download history"),
          compression: find("Compression"),
          integrity: find("Frame integrity"),
          logs: find("Session logs"),
        };
      });
      assert.ok(Object.values(boxes).every(Boolean), JSON.stringify(boxes));

      // Three rows, each a left/right pair sharing one top edge and one bottom edge.
      // Panels are direct grid children and `.panel { height: 100% }` makes both fill
      // the row, so a row is exactly as tall as its taller panel — that equality is the
      // symmetry being asserted.
      const rows = [
        ["Data-loss diagnostics", boxes.loss, "Per-underlying health", boxes.health],
        ["Frame integrity", boxes.integrity, "Session history", boxes.sessions],
        ["Download history", boxes.storage, "Compression", boxes.compression],
      ];
      for (const [leftName, left, rightName, right] of rows) {
        assert.equal(left.top, right.top, `${leftName}/${rightName} tops differ`);
        assert.equal(left.bottom, right.bottom, `${leftName}/${rightName} bottoms differ`);
        assert.ok(right.left > left.left, `${rightName} must sit right of ${leftName}`);
      }

      // Rows stack in order rather than collapsing onto one line.
      assert.ok(boxes.loss.top < boxes.integrity.top);
      assert.ok(boxes.integrity.top < boxes.storage.top);

      // Session logs is a full-width strip BELOW the grid, not a paired panel: pairing a
      // log ticker into a row forced every other row taller to match it.
      assert.ok(boxes.logs.top > boxes.storage.top, "logs must sit below the last grid row");
      assert.ok(
        boxes.logs.width > boxes.health.width,
        "logs must span both columns",
      );

      // Left column narrower than the right (1fr vs 1.25fr): the right carries the wide
      // data tables. Ratio checked loosely so gutter/padding changes don't break it.
      const ratio = boxes.health.width / boxes.loss.width;
      assert.ok(ratio > 1.1 && ratio < 1.45, `expected ~1.25 column ratio, got ${ratio.toFixed(2)}`);

      // Every left panel shares one column edge and width; likewise every right panel.
      for (const [name, box] of [["Frame integrity", boxes.integrity], ["Download history", boxes.storage]]) {
        assert.equal(box.left, boxes.loss.left, `${name} left edge off-column`);
        assert.equal(box.width, boxes.loss.width, `${name} width off-column`);
      }
      for (const [name, box] of [["Session history", boxes.sessions], ["Compression", boxes.compression]]) {
        assert.equal(box.left, boxes.health.left, `${name} left edge off-column`);
        assert.equal(box.width, boxes.health.width, `${name} width off-column`);
      }
    } finally {
      await page.close();
    }
  });

  for (const width of [1280, 1440, 1920]) {
    it(`per-underlying health has compact connection dots and no horizontal scroll at ${width}px`, async () => {
      const page = await openMonitorWithTelemetry(width, 900);
      try {
        const geometry = await page.evaluate(() => {
          const table = document.querySelector(".monitor-health-table");
          const frame = table?.parentElement;
          const tableRect = table?.getBoundingClientRect();
          const frameRect = frame?.getBoundingClientRect();
          const overflowingCellDetails = table
            ? [...table.querySelectorAll("th, td")]
              .filter((cell) => cell.scrollWidth > cell.clientWidth + 1)
              .map((cell) => ({
                text: cell.textContent?.trim(),
                clientWidth: cell.clientWidth,
                scrollWidth: cell.scrollWidth,
              }))
            : [];
          return {
            dotCount: table?.querySelectorAll(
              '[role="img"][aria-label*="connection"]',
            ).length,
            overflowingCellDetails,
            frameClientWidth: frame?.clientWidth,
            frameScrollWidth: frame?.scrollWidth,
            frameOverflowX: frame ? getComputedStyle(frame).overflowX : null,
            tableWidth: tableRect?.width,
            frameWidth: frameRect?.width,
          };
        });

        assert.equal(geometry.dotCount, 5);
        assert.deepEqual(
          geometry.overflowingCellDetails,
          [],
          JSON.stringify(geometry),
        );
        assert.equal(geometry.frameOverflowX, "hidden");
        assert.ok(
          geometry.frameScrollWidth <= geometry.frameClientWidth + 1,
          JSON.stringify(geometry),
        );
        assert.ok(
          geometry.tableWidth <= geometry.frameWidth + 1,
          JSON.stringify(geometry),
        );
      } finally {
        await page.close();
      }
    });
  }

  for (const width of [768, 1024, 1279]) {
    it(`per-underlying health keeps complete disclosures without horizontal scroll at ${width}px`, async () => {
      const page = await openMonitorWithTelemetry(width, 900);
      try {
        const toggles = await page.$$(
          'button[aria-label$="stream details"]',
        );
        const visibleToggles = [];
        for (const toggle of toggles) {
          if (await toggle.evaluate((element) =>
            element.getBoundingClientRect().width > 0,
          )) {
            visibleToggles.push(toggle);
          }
        }
        assert.equal(visibleToggles.length, 5);
        await visibleToggles[0].click();
        await page.waitForSelector("#stream-NIFTY:not([hidden])");

        const state = await page.evaluate(() => {
          const table = document.querySelector(".monitor-health-table");
          const tableFrame = table?.parentElement;
          const detail = document.querySelector("#stream-NIFTY");
          return {
            tableFrameDisplay: tableFrame ? getComputedStyle(tableFrame).display : null,
            detailText: detail?.textContent,
            documentWidth: document.documentElement.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
          };
        });
        assert.equal(state.tableFrameDisplay, "none");
        assert.match(state.detailText ?? "", /Full-session loss/);
        assert.match(state.detailText ?? "", /Bytes \/ frame/);
        assert.match(state.detailText ?? "", /Projected EOD/);
        assert.ok(
          state.documentWidth <= state.viewportWidth + 1,
          JSON.stringify(state),
        );
      } finally {
        await page.close();
      }
    });
  }

  it("monitor stacks into one full-width column on a phone", async () => {
    const page = await openPage("/monitor", 390, 844);
    try {
      const boxes = await page.evaluate(() => {
        const titles = [
          "Data-loss diagnostics",
          "Per-underlying health",
          "Frame integrity",
          "Session history",
          "Download history",
          "Compression",
          "Session logs",
        ];
        return titles.map((title) => {
          const heading = [...document.querySelectorAll("h2")].find(
            (item) => item.textContent?.trim() === title,
          );
          const rect = heading?.closest(".panel")?.getBoundingClientRect();
          return rect
            ? { title, top: Math.round(rect.top), left: Math.round(rect.left), width: Math.round(rect.width) }
            : null;
        });
      });

      assert.ok(boxes.every(Boolean), JSON.stringify(boxes));

      // One column: identical left edge and width for every panel, and each panel starts
      // below the previous one — no side-by-side pairing at this width.
      const { left, width } = boxes[0];
      for (const box of boxes) {
        assert.equal(box.left, left, `${box.title} breaks the single column`);
        assert.equal(box.width, width, `${box.title} width differs on mobile`);
      }
      for (let i = 1; i < boxes.length; i += 1) {
        assert.ok(
          boxes[i].top > boxes[i - 1].top,
          `${boxes[i].title} does not stack below ${boxes[i - 1].title}`,
        );
      }
    } finally {
      await page.close();
    }
  });

  it("monitor header stays below the mobile navigation while scrolling", async () => {
    const page = await openPage("/monitor", 375, 667);
    try {
      await page.evaluate(() => window.scrollTo(0, 20));
      const overlap = await page.evaluate(() => {
        const navigation = document.querySelector("nav");
        const header = document.querySelector(".monitor-header");
        if (!navigation || !header) return null;
        const navigationRect = navigation.getBoundingClientRect();
        const headerRect = header.getBoundingClientRect();
        const x = Math.max(headerRect.left + 8, 8);
        const y = Math.min(navigationRect.bottom - 4, headerRect.bottom - 4);
        return {
          intersects: headerRect.top < navigationRect.bottom,
          topElement: document.elementFromPoint(x, y)?.closest("nav") ? "navigation" : "other",
        };
      });
      assert.ok(overlap, "navigation/header geometry unavailable");
      if (overlap.intersects) {
        assert.equal(overlap.topElement, "navigation", "monitor header painted above navigation");
      }
    } finally {
      await page.close();
    }
  });

  it("navigation and page content share progressive ultrawide edges", async () => {
    for (const width of [1920, 2400, 3200]) {
      const page = await openPage("/", width, 1080);
      try {
        const edges = await page.evaluate(() => {
          const navigation = document.querySelector("nav > div")?.getBoundingClientRect();
          const main = document.querySelector("main")?.getBoundingClientRect();
          return {
            navigation: navigation ? [Math.round(navigation.left), Math.round(navigation.right)] : null,
            main: main ? [Math.round(main.left), Math.round(main.right)] : null,
          };
        });
        assert.deepEqual(edges.navigation, edges.main, `${width}px: ${JSON.stringify(edges)}`);
      } finally {
        await page.close();
      }
    }
  });

  it("all route headers begin on the shared page edge", async () => {
    const headerTops = [];
    for (const path of ROUTES) {
      const page = await openPage(path, 1440, 900);
      try {
        const edges = await page.evaluate(() => {
          const main = document.querySelector("main")?.getBoundingClientRect();
          const header = document.querySelector(".page-header")?.getBoundingClientRect();
          const heading = document.querySelector("h1")?.getBoundingClientRect();
          const actions = document.querySelector(".page-header-actions")?.getBoundingClientRect();
          return {
            main: main ? Math.round(main.left + 16) : null,
            heading: heading ? Math.round(heading.left) : null,
            headerTop: header ? Math.round(header.top) : null,
            actionsTop: actions ? Math.round(actions.top) : null,
          };
        });
        assert.equal(edges.heading, edges.main, `${path}: ${JSON.stringify(edges)}`);
        if (edges.headerTop !== null) headerTops.push(edges.headerTop);
        if (edges.actionsTop !== null) {
          assert.equal(edges.actionsTop, edges.headerTop, `${path}: ${JSON.stringify(edges)}`);
        }
      } finally {
        await page.close();
      }
    }
    assert.equal(new Set(headerTops).size, 1, JSON.stringify(headerTops));
  });

  it("home secondary destinations form an equal desktop row", async () => {
    const page = await openPage("/", 1440, 900);
    try {
      const boxes = await page.evaluate(() =>
        ["/option-chain", "/stocks", "/login"].map((href) => {
          const rect = document.querySelector(`main a[href="${href}"]`)?.getBoundingClientRect();
          return rect ? {
            top: Math.round(rect.top),
            bottom: Math.round(rect.bottom),
            width: Math.round(rect.width),
          } : null;
        }),
      );
      assert.ok(boxes.every(Boolean), JSON.stringify(boxes));
      assert.equal(new Set(boxes.map((box) => box.top)).size, 1, JSON.stringify(boxes));
      assert.equal(new Set(boxes.map((box) => box.bottom)).size, 1, JSON.stringify(boxes));
      assert.ok(Math.max(...boxes.map((box) => box.width)) - Math.min(...boxes.map((box) => box.width)) <= 1);
    } finally {
      await page.close();
    }
  });

  it("a single monitor alert fills the available row", async () => {
    const page = await openPage("/monitor", 1440, 900);
    try {
      const widths = await page.evaluate(() => {
        const alerts = document.querySelector('[aria-label="Operational alerts"]');
        const alert = alerts?.firstElementChild;
        return {
          row: alerts ? Math.round(alerts.getBoundingClientRect().width) : null,
          alert: alert ? Math.round(alert.getBoundingClientRect().width) : null,
        };
      });
      assert.equal(widths.alert, widths.row, JSON.stringify(widths));
    } finally {
      await page.close();
    }
  });

  it("paired monitor alerts keep equal visible card heights", async () => {
    const page = await openMonitorWithTelemetry(1440, 900);
    try {
      await page.evaluate(() => {
        const socketEntry = Object.entries(window.__testSockets).find(([url]) =>
          url.endsWith("/ws/capture-status"),
        );
        socketEntry?.[1]?.onmessage?.({
          data: JSON.stringify({
            type: "CaptureStatus",
            payload: {
              per_underlying: [],
              global: {
                fps: 0,
                exhausted: true,
                stale: true,
                data_age_ms: 8_000,
              },
            },
          }),
        });
      });
      await page.waitForFunction(() =>
        document.querySelectorAll(
          '[aria-label="Operational alerts"] .state-message',
        ).length === 2,
      );
      const heights = await page.evaluate(() => {
        const alerts = document.querySelector('[aria-label="Operational alerts"]');
        if (!alerts) return null;
        return [...alerts.querySelectorAll(".state-message")].map((message) =>
          Math.round(message.getBoundingClientRect().height),
        );
      });
      assert.ok(heights && heights.length === 2, JSON.stringify(heights));
      assert.equal(heights[0], heights[1], JSON.stringify(heights));
    } finally {
      await page.close();
    }
  });

  it("route metadata is specific to each data surface", async () => {
    const optionPage = await openPage("/option-chain", 1440, 900);
    const stockPage = await openPage("/stocks", 1440, 900);
    try {
      assert.match(await optionPage.title(), /^Option Chain \| /);
      assert.match(await stockPage.title(), /^Stocks \| /);
    } finally {
      await optionPage.close();
      await stockPage.close();
    }
  });

  it("logs use a native dialog with focus restoration", async () => {
    const page = await openPage("/monitor", 1024, 768);
    try {
      const hasTrigger = await page.evaluate(() =>
        [...document.querySelectorAll("button")].some((item) => item.textContent?.includes("Open log viewer")),
      );
      assert.equal(hasTrigger, true);
      await page.evaluate(() => {
        const target = [...document.querySelectorAll("button")].find((item) => item.textContent?.includes("Open log viewer"));
        target?.click();
      });
      await page.waitForSelector("dialog[open]");
      assert.equal(await page.$eval("dialog", (dialog) => dialog.open), true);
      assert.match(await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? ""), /^Close Session logs$/);
      await page.keyboard.press("Escape");
      await page.waitForFunction(() => !document.querySelector("dialog")?.open);
      assert.equal(await page.$eval("dialog", (dialog) => dialog.open), false);
      assert.match(await page.evaluate(() => document.activeElement?.textContent ?? ""), /Open log viewer/);
    } finally {
      await page.close();
    }
  });

  it("all routes pass Axe serious and critical checks", async () => {
    for (const path of ROUTES) {
      const page = await openPage(path, 1440, 900);
      try {
        const results = await new AxePuppeteer(page).analyze();
        const blocking = results.violations.filter((violation) => ["serious", "critical"].includes(violation.impact));
        assert.deepEqual(blocking, [], `${path}: ${blocking.map((item) => item.id).join(", ")}`);
      } finally {
        await page.close();
      }
    }
  });
});
