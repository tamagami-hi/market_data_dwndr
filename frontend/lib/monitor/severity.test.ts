import { describe, expect, test } from "vitest";

import { captureSeverity, fpsSeverity, lossSeverity } from "@/lib/monitor/severity";

describe("monitor severity", () => {
  test("reserves danger for stopped or exhausted capture states", () => {
    expect(captureSeverity({ exhausted: true, stale: false, degraded: false })).toBe("danger");
    expect(captureSeverity({ exhausted: false, stale: true, degraded: false })).toBe("danger");
  });

  test("uses warning for a recoverable degraded feed", () => {
    expect(captureSeverity({ exhausted: false, stale: false, degraded: true })).toBe("warning");
  });

  test("treats the expected one hertz band as healthy", () => {
    expect(fpsSeverity(0.9)).toBe("success");
    expect(fpsSeverity(1.1)).toBe("success");
    expect(fpsSeverity(0)).toBe("danger");
    expect(fpsSeverity(0.7)).toBe("warning");
  });

  test("uses elapsed loss thresholds consistently", () => {
    expect(lossSeverity(0)).toBe("neutral");
    expect(lossSeverity(0.01)).toBe("warning");
    expect(lossSeverity(1)).toBe("danger");
  });
});
