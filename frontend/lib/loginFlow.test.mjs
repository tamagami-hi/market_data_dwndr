import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("./loginFlow.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  initialLoginFlowState,
  isValidTotp,
  loginFlowReducer,
  parseRiskFreeRate,
} = await import(moduleUrl);

test("advances through start, TOTP, rate, and success", () => {
  const starting = loginFlowReducer(initialLoginFlowState, { type: "start" });
  const totp = loginFlowReducer(starting, {
    type: "started",
    attemptId: "attempt",
  });
  const rate = loginFlowReducer(totp, { type: "totpAccepted" });
  const success = loginFlowReducer(rate, { type: "succeeded" });

  assert.equal(starting.step, "starting");
  assert.equal(totp.step, "totp");
  assert.equal(totp.attemptId, "attempt");
  assert.equal(rate.step, "rate");
  assert.equal(success.step, "success");
});

test("accepts only six ASCII digits for TOTP", () => {
  assert.equal(isValidTotp("123456"), true);
  assert.equal(isValidTotp("12345"), false);
  assert.equal(isValidTotp("12ab56"), false);
  assert.equal(isValidTotp("१२३४५६"), false);
});

test("parses only finite non-negative risk-free rates", () => {
  assert.equal(parseRiskFreeRate("0.0691"), 0.0691);
  assert.equal(parseRiskFreeRate(""), null);
  assert.equal(parseRiskFreeRate("-0.01"), null);
  assert.equal(parseRiskFreeRate("Infinity"), null);
});

test("preserves the current step while showing a retryable error", () => {
  const state = loginFlowReducer(initialLoginFlowState, {
    type: "started",
    attemptId: "attempt",
  });
  const failed = loginFlowReducer(state, { type: "failed", message: "Invalid code" });

  assert.equal(failed.step, "totp");
  assert.equal(failed.attemptId, "attempt");
  assert.equal(failed.error, "Invalid code");
});

test("clears a consumed attempt after a fatal flow error", () => {
  const state = loginFlowReducer(initialLoginFlowState, {
    type: "started",
    attemptId: "attempt",
  });
  const failed = loginFlowReducer(state, {
    type: "failedAndReset",
    message: "Invalid code",
  });

  assert.equal(failed.step, "idle");
  assert.equal(failed.attemptId, null);
  assert.equal(failed.error, "Invalid code");
});

test("resumes an existing rate-confirmation attempt", () => {
  const resumed = loginFlowReducer(initialLoginFlowState, {
    type: "started",
    attemptId: "attempt",
    backendStep: "awaiting_risk_free_rate",
  });

  assert.equal(resumed.step, "rate");
  assert.equal(resumed.attemptId, "attempt");
});

test("external access token skips TOTP and advances directly to rate", () => {
  const started = loginFlowReducer(initialLoginFlowState, {
    type: "started",
    attemptId: "external-attempt",
    backendStep: "awaiting_risk_free_rate",
    method: "shared_session",
  });

  assert.equal(started.step, "rate");
  assert.equal(started.attemptId, "external-attempt");
  assert.equal(started.method, "shared_session");
});

test("missing external session preserves the local TOTP branch", () => {
  const started = loginFlowReducer(initialLoginFlowState, {
    type: "started",
    attemptId: "local-attempt",
    backendStep: "awaiting_totp",
    method: "local_credentials",
  });

  assert.equal(started.step, "totp");
  assert.equal(started.method, "local_credentials");
});
