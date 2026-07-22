import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("./operatorAuth.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { initialOperatorAuthState, isValidOperatorToken, operatorAuthReducer } = await import(
  moduleUrl
);

test("moves from checking to locked and then unlocked", () => {
  const locked = operatorAuthReducer(initialOperatorAuthState, {
    type: "checked",
    isUnlocked: false,
  });
  const unlocking = operatorAuthReducer(locked, { type: "unlocking" });
  const unlocked = operatorAuthReducer(unlocking, { type: "unlocked" });

  assert.equal(locked.phase, "locked");
  assert.equal(unlocking.phase, "unlocking");
  assert.equal(unlocked.phase, "unlocked");
  assert.equal(unlocked.error, null);
});

test("keeps unlock errors visible without retaining the supplied token", () => {
  const failed = operatorAuthReducer(initialOperatorAuthState, {
    type: "failed",
    message: "Invalid operator credential",
  });

  assert.deepEqual(failed, {
    phase: "locked",
    error: "Invalid operator credential",
  });
  assert.equal("token" in failed, false);
});

test("requires a 32 to 256 character operator token", () => {
  assert.equal(isValidOperatorToken("A".repeat(32)), true);
  assert.equal(isValidOperatorToken("A".repeat(256)), true);
  assert.equal(isValidOperatorToken("A".repeat(31)), false);
  assert.equal(isValidOperatorToken("A".repeat(257)), false);
  assert.equal(isValidOperatorToken(" ".repeat(32)), false);
});
