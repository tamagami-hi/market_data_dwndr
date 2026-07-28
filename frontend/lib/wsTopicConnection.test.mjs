/**
 * WebSocket stream hygiene.
 *
 * The dashboard mounts several consumers against the same topic (a ConnectionDot for the
 * indicator plus one or more envelope handlers for the data). Each consumer calls
 * acquire()/release() independently, so the risk is one socket per consumer — duplicate
 * streams multiplying bandwidth for identical data. These tests pin the reference-counted
 * behaviour: exactly one socket per topic no matter how many consumers, and the socket is
 * closed only when the last consumer leaves.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

/** Sockets opened during a test, so we can count duplicates. */
const opened = [];

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this.closed = false;
    opened.push(this);
  }
  close() {
    this.closed = true;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }
  send() {}
}

globalThis.WebSocket = FakeWebSocket;
globalThis.window = globalThis;
globalThis.location = { protocol: "http:", host: "127.0.0.1:3789" };

async function loadModule(relPath) {
  const source = await readFile(new URL(relPath, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  // Strip every "@/..." import: this test exercises the connection factory's ref
  // counting in isolation, so the URL helper is stubbed and type-only imports vanish
  // during transpilation anyway.
  const stubbed = compiled
    .replace(/import\s*\{[^}]*\}\s*from\s*["']@\/[^"']*["'];?/g, "")
    .replace(/^/, "const getBackendWsUrl = () => 'ws://test';\n");
  const url = `data:text/javascript;base64,${Buffer.from(stubbed).toString("base64")}`;
  return import(url);
}

const mod = await loadModule("./wsTopicConnection.ts");

test("many consumers of one topic share a single socket", () => {
  opened.length = 0;
  const conn = mod.marketDataConnection;

  // Three independent consumers, as a page with a dot + two handlers would do.
  conn.acquire();
  conn.acquire();
  conn.acquire();

  assert.equal(opened.length, 1, `expected 1 socket for 3 consumers, got ${opened.length}`);

  conn.release();
  conn.release();
  assert.equal(opened[0].closed, false, "socket must stay open while a consumer remains");

  conn.release();
  assert.equal(opened[0].closed, true, "socket must close once the last consumer leaves");
});

test("re-acquiring after full release opens exactly one new socket", () => {
  opened.length = 0;
  const conn = mod.stocksConnection;

  conn.acquire();
  conn.release();
  conn.acquire();

  assert.equal(opened.length, 2, "one socket per connect cycle");
  assert.equal(opened[1].closed, false);
  conn.release();
});

test("each exported topic is a distinct connection, so topics cannot cross-subscribe", () => {
  const topics = [
    "marketDataConnection",
    "stocksConnection",
    "captureStatusConnection",
    "sessionConnection",
    "historicalJobsConnection",
  ];
  const objects = topics.map((name) => {
    assert.ok(mod[name], `${name} is not exported`);
    return mod[name];
  });
  const unique = new Set(objects);
  assert.equal(unique.size, topics.length, "topic connections must not be shared objects");
});

test("an unacquired connection never opens a socket", () => {
  opened.length = 0;
  // historical-jobs is exported but currently has no UI consumer; it must stay dormant
  // rather than holding an idle stream open.
  mod.historicalJobsConnection.getState();
  assert.equal(opened.length, 0, "a connection with no consumers must not connect");
});
