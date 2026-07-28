import { describe, expect, test } from "vitest";

import { normalizeAuthStatus } from "@/lib/api";
import { downloaderInitialization } from "@/lib/downloader/initialization";

describe("downloader initialization", () => {
  test("describes loading and offline states", () => {
    expect(downloaderInitialization(undefined)).toMatchObject({ progress: 8, tone: "neutral" });
    expect(downloaderInitialization(null)).toMatchObject({ progress: 0, tone: "danger" });
  });

  test("surfaces a missing secure broker as an actionable prerequisite", () => {
    const state = downloaderInitialization({
      configured: true,
      authenticated: false,
      external_token_source_configured: false,
    });
    expect(state.headline).toBe("Secure token broker is not configured");
    expect(state.stages[1]).toMatchObject({ state: "error" });
  });

  test("reports a running capture with complete immutable stages", () => {
    const status = {
      configured: true,
      authenticated: true,
      external_token_source_configured: true,
      capture_ready: true,
      capture: { available: true, running: true, tokens: 321, trading_date: "2026-07-29" },
    };
    const before = structuredClone(status);
    const state = downloaderInitialization(status);
    expect(state).toMatchObject({ progress: 100, headline: "Downloader is running", tone: "success" });
    expect(state.stages.every((stage) => stage.state === "complete")).toBe(true);
    expect(status).toEqual(before);
  });

  test("covers unconfigured, pending, prerequisite, ready, and fallback-broker states", () => {
    expect(downloaderInitialization({ configured: false, authenticated: false })).toMatchObject({
      progress: 15,
      tone: "danger",
    });
    expect(
      downloaderInitialization({
        configured: true,
        authenticated: false,
        external_token_source_configured: true,
        automation: { last_broker_poll_at: 1, last_error: "Token pending" },
      }),
    ).toMatchObject({ progress: 55, tone: "warning" });
    expect(
      downloaderInitialization({
        configured: true,
        authenticated: true,
        external_token_source_configured: false,
        capture_ready: false,
      }),
    ).toMatchObject({ progress: 75, tone: "warning" });
    expect(
      downloaderInitialization({
        configured: true,
        authenticated: true,
        external_token_source_configured: true,
        capture_ready: true,
        capture: { available: true, running: false },
      }),
    ).toMatchObject({ progress: 100, headline: "Downloader initialized", tone: "success" });
  });

  test("normalizes auth status display fields at the REST boundary", () => {
    expect(normalizeAuthStatus(null)).toBeNull();
    expect(normalizeAuthStatus({ configured: true, authenticated: "yes" })).toBeNull();
    expect(
      normalizeAuthStatus({
        configured: true,
        authenticated: false,
        market_phase: { unsafe: true },
        automation: { last_action: { unsafe: true }, last_broker_poll_at: "soon" },
      }),
    ).toMatchObject({
      configured: true,
      authenticated: false,
      market_phase: undefined,
      automation: { last_action: null, last_broker_poll_at: null },
    });
  });
});
