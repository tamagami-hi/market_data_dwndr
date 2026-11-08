import { describe, expect, test } from "vitest";

import {
  LOSS_FULL_SEVERITY_PCT,
  formatLoss,
  lossColor,
  lossRowStyle,
  lossSeverity,
} from "@/lib/monitor/lossColor";

describe("lossSeverity", () => {
  test("does not highlight a recorded loss at or below 1%", () => {
    expect(lossSeverity(0)).toBeNull();
    expect(lossSeverity(0.463)).toBeNull();
    expect(lossSeverity(1)).toBeNull();
  });

  test("returns null when no session was recorded", () => {
    expect(lossSeverity(null)).toBeNull();
    expect(lossSeverity(undefined)).toBeNull();
    expect(lossSeverity(Number.NaN)).toBeNull();
  });

  test("grows with the loss and saturates at the full-severity loss", () => {
    const small = lossSeverity(2.152)!;
    const large = lossSeverity(13.784)!;
    expect(small).toBeGreaterThan(0);
    expect(large).toBeGreaterThan(small);
    expect(lossSeverity(LOSS_FULL_SEVERITY_PCT)).toBe(1);
    expect(lossSeverity(80)).toBe(1);
  });
});

test("the colour blends from amber toward red with severity", () => {
  expect(lossColor(0)).toBe("color-mix(in oklab, var(--danger) 0%, var(--warning))");
  expect(lossColor(1)).toBe("color-mix(in oklab, var(--danger) 100%, var(--warning))");
  expect(lossColor(lossSeverity(13.784)!)).toContain("var(--danger) 67%");
});

test("row style tints only highlighted rows, deeper for bigger losses", () => {
  expect(lossRowStyle(0.5)).toBeUndefined();
  const mild = lossRowStyle(2.152)!;
  const severe = lossRowStyle(13.784)!;
  expect(mild.boxShadow).toContain("inset 3px 0 0");
  const tint = (style: { backgroundColor?: unknown }) =>
    Number(/ (\d+)%, transparent\)$/.exec(String(style.backgroundColor))?.[1]);
  expect(tint(severe)).toBeGreaterThan(tint(mild));
});

test("formats the loss or a dash", () => {
  expect(formatLoss(13.784)).toBe("13.78%");
  expect(formatLoss(null)).toBe("--");
});
