import { expect, test } from "vitest";

import { automationMessage } from "@/lib/automationStatus";

test("describes the morning broker retry and ready states", () => {
  expect(automationMessage({ phase: "auth_window", last_error: "shared token is not ready" }, false)).toBe(
    "Shared token is not ready yet. The server will retry during the 08:30-09:00 IST window.",
  );
  expect(automationMessage({ phase: "capture_window" }, true)).toBe(
    "Daily authentication is ready. Capture runs automatically from 09:00 to 15:30 IST.",
  );
});

test("describes every automation phase and fallback", () => {
  expect(automationMessage({ phase: "auth_window" }, false)).toMatch(/checking calspread/);
  expect(automationMessage({ phase: "capture_window" }, false)).toMatch(/No capture-ready session/);
  expect(
    automationMessage({ phase: "eod", eod_in_progress_date: "2026-07-29" }, false),
  ).toMatch(/running for 2026-07-29/);
  expect(
    automationMessage({ phase: "eod", eod_completed_date: "2026-07-29" }, false),
  ).toMatch(/completed for 2026-07-29/);
  expect(automationMessage({ phase: "eod" }, false)).toMatch(/Waiting for end-of-day/);
  expect(automationMessage(undefined, false)).toMatch(/08:30 IST/);
});
