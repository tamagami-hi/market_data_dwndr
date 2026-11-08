import { expect, test } from "vitest";

import {
  activeLossCauses,
  lossCauses,
  lossTone,
  scheduleCompleteness,
  scheduleLoss,
} from "@/lib/monitor/lossCauses";

test("returns null when the record has no scheduled breakdown", () => {
  expect(lossCauses({})).toBeNull();
  expect(activeLossCauses({})).toEqual([]);
});

test("splits the 2026-11-05 outage into causes that reconcile with the missing total", () => {
  const causes = lossCauses({
    missing_seconds: 3_184,
    stale_feed_seconds: 54,
    downtime_seconds: 3_130,
    restart_downtime_seconds: 98,
    not_running_seconds: 3_032,
  })!;
  const bySeconds = Object.fromEntries(causes.map((cause) => [cause.key, cause.seconds]));
  expect(bySeconds).toEqual({
    stale_feed: 54,
    process_restart: 98,
    not_running: 3_032,
    downtime: 0,
    awaiting_first_tick: 0,
    write_path: 0,
    unclassified: 0,
    unaccounted: 0,
  });
  expect(causes.reduce((sum, cause) => sum + cause.seconds, 0)).toBe(3_184);
});

test("keeps unsplit downtime and an unattributed remainder visible", () => {
  const causes = activeLossCauses({ missing_seconds: 700, downtime_seconds: 500, stale_feed_seconds: 100 });
  expect(causes.map((cause) => [cause.key, cause.seconds])).toEqual([
    ["stale_feed", 100],
    ["downtime", 500],
    ["unaccounted", 100],
  ]);
});

test("ignores negative and non-finite parts", () => {
  const causes = activeLossCauses({
    missing_seconds: 10,
    stale_feed_seconds: -5,
    write_path_seconds: Number.NaN,
    unclassified_seconds: 10,
  });
  expect(causes.map((cause) => cause.key)).toEqual(["unclassified"]);
});

test("schedule loss falls back to the process-relative figure only for old records", () => {
  expect(scheduleLoss({ scheduled_loss_pct: 2.152, data_loss_pct: 0.004 })).toBe(2.152);
  expect(scheduleLoss({ data_loss_pct: 0.75 })).toBe(0.75);
});

test("completeness is captured over the elapsed schedule", () => {
  expect(
    scheduleCompleteness({ captured_seconds: 19_916, scheduled_seconds: 23_100, scheduled_seconds_elapsed: 23_100 }),
  ).toBeCloseTo(86.216, 3);
  // A live row mid-session is measured against what has elapsed, not the whole day.
  expect(
    scheduleCompleteness({ captured_seconds: 3_600, scheduled_seconds: 23_100, scheduled_seconds_elapsed: 3_600 }),
  ).toBe(100);
  expect(scheduleCompleteness({ captured_seconds: 10, scheduled_seconds: 20 })).toBe(50);
  expect(scheduleCompleteness({ scheduled_seconds: 23_100 })).toBeNull();
  expect(scheduleCompleteness({ captured_seconds: 0, scheduled_seconds: 0 })).toBeNull();
});

test("loss colour follows the value as displayed", () => {
  // 0.004% prints as "0.00%" and must not be amber beside it.
  expect(lossTone(0.004)).toBe("neutral");
  expect(lossTone(0.048)).toBe("warning");
  expect(lossTone(0.999)).toBe("danger"); // shown as "1.00%"
  expect(lossTone(13.784)).toBe("danger");
  expect(lossTone(0)).toBe("neutral");
});
