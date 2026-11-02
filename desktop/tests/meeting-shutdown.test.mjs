import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("../meeting-main.mjs", import.meta.url), "utf8");
const finishSource = source.slice(source.indexOf("async function finishCapture("), source.indexOf("function trusted("));
const quitSource = source.slice(source.indexOf("async function quit("), source.indexOf("async function main("));

function lifecycle() {
  const events = [];
  let expire;
  const context = vm.createContext({
    currentSession: "meeting-with-unsaved-audio",
    selecting: false,
    quitting: false,
    finalizing: undefined,
    armed: true,
    setTimeout: (callback) => { expire = callback; return 1; },
    clearTimeout() {},
    window: { isDestroyed: () => false, destroy: () => events.push("destroy"), webContents: { send: () => events.push("flush-request") } },
    nativeAudio: { stop: async () => events.push("stop-native") },
    spoolController: { close: () => events.push("close-spool") },
    dialog: { showMessageBox: async () => events.push("warning") },
    RecallSdk: { shutdown: async () => events.push("shutdown-sdk") },
    server: { close: () => events.push("close-server") },
    app: { quit: () => events.push("quit") },
  });
  vm.runInContext(finishSource + quitSource, context);
  return { context, events, expire: () => expire() };
}

test("graceful quit cannot destroy unsaved renderer audio after finalization timeout", async () => {
  const runtime = lifecycle();
  const closing = vm.runInContext("quit()", runtime.context);
  runtime.expire();
  await closing;
  assert.deepEqual(runtime.events, ["flush-request", "stop-native", "warning"]);
  assert.equal(runtime.context.quitting, false);
  assert.equal(runtime.context.currentSession, "meeting-with-unsaved-audio");
  assert.equal(runtime.context.finalizing, undefined);
});

test("graceful quit destroys renderer only after successful finalization acknowledgement", async () => {
  const runtime = lifecycle();
  const closing = vm.runInContext("quit()", runtime.context);
  vm.runInContext("currentSession = undefined; finalizing.resolve();", runtime.context);
  await closing;
  assert.deepEqual(runtime.events, ["flush-request", "close-spool", "destroy", "shutdown-sdk", "close-server", "quit"]);
});

test("quit during pending backend startup waits for renderer finalization acknowledgement", async () => {
  const runtime = lifecycle();
  runtime.context.currentSession = undefined;
  runtime.context.selecting = true;
  const closing = vm.runInContext("quit()", runtime.context);
  assert.deepEqual(runtime.events, ["flush-request"]);
  assert.equal(runtime.context.armed, false);
  vm.runInContext("currentSession = 'newly-created-session'; selecting = false;", runtime.context);
  assert.ok(!runtime.events.includes("destroy"));
  vm.runInContext("currentSession = undefined; finalizing.resolve();", runtime.context);
  await closing;
  assert.ok(runtime.events.includes("destroy"));
});

test("quit during pending startup timeout preserves window rather than silently abandoning startup", async () => {
  const runtime = lifecycle();
  runtime.context.currentSession = undefined;
  runtime.context.selecting = true;
  const closing = vm.runInContext("quit()", runtime.context);
  runtime.expire();
  await closing;
  assert.deepEqual(runtime.events, ["flush-request", "warning"]);
  assert.equal(runtime.context.quitting, false);
});

test("meeting disappearance during backend creation returns session identity for renderer drain", async () => {
  let release;
  let available = true;
  const events = [];
  const context = vm.createContext({
    command: { type: "start", detected_id: "meet-room" },
    currentSession: undefined, selectedMeeting: undefined, selecting: false,
    armed: false, finalizing: undefined, quitting: false,
    spoolController: { ready: async () => {}, status: () => ({}), authorize: async () => {} },
    discovery: { get: () => available ? { id: "meet-room", title: "Meeting" } : undefined },
    client: { command: () => new Promise((resolve) => { release = resolve; }) },
    window: { webContents: { send: (channel, event) => events.push([channel, event.action]) } },
  });
  const startBlock = source.slice(source.indexOf('    if (command?.type === "start")'), source.indexOf('    if (command?.type === "open_source")'));
  const pending = vm.runInContext(`(async () => { ${startBlock} })()`, context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.selecting, true);
  available = false;
  release({ id: "late-session", state: "capturing" });
  const result = await pending;
  assert.equal(result.id, "late-session");
  assert.equal(context.currentSession, "late-session");
  assert.equal(context.armed, false);
  assert.equal(context.selecting, false);
  assert.deepEqual(events, [["meeting:stop", "stop"]]);
});
