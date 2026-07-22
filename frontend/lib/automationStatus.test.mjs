import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("./automationStatus.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { automationMessage } = await import(moduleUrl);

test("describes the morning broker retry and ready states", () => {
  assert.equal(
    automationMessage({ phase: "auth_window", last_error: "shared token is not ready" }, false, false),
    "Shared token is not ready yet. The server will retry during the 08:30–09:00 IST window.",
  );
  assert.equal(
    automationMessage({ phase: "capture_window" }, true, false),
    "Daily authentication is ready. Capture runs automatically from 09:00 to 15:30 IST.",
  );
});

test("makes a stale yield an explicit operator action", () => {
  assert.equal(
    automationMessage({ phase: "capture_window" }, false, true),
    "Update the 10-year bond yield before capture can start.",
  );
});
