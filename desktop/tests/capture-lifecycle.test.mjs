import test from "node:test";
import assert from "node:assert/strict";
import { CaptureLifecycle, captureStatus } from "../meeting/capture-lifecycle.mjs";

test("capture operations are mutually exclusive and release after success or failure", () => {
  for (const phase of ["starting", "pausing", "ending"]) {
    const lifecycle = new CaptureLifecycle();
    const operation = lifecycle.begin(phase);
    assert.equal(lifecycle.phase, phase);
    assert.equal(lifecycle.begin("starting"), undefined);
    assert.equal(lifecycle.begin(phase), undefined);
    assert.equal(lifecycle.finish(operation), true);
    assert.equal(lifecycle.phase, "idle");
    assert.equal(lifecycle.busy, false);
    assert.equal(lifecycle.finish(operation), false);
  }
});

test("setup failure may pause startup but stale startup completion cannot release pause", () => {
  const lifecycle = new CaptureLifecycle();
  const starting = lifecycle.begin("starting");
  const pausing = lifecycle.begin("pausing");
  assert.ok(pausing);
  assert.equal(lifecycle.finish(starting), false);
  assert.equal(lifecycle.phase, "pausing");
  assert.equal(lifecycle.begin("pausing"), undefined);
  lifecycle.finish(pausing);
  const ending = lifecycle.begin("ending");
  assert.equal(lifecycle.begin("pausing"), undefined);
  lifecycle.finish(ending);
});

test("server capturing does not imply physical capture after failed End or Pause", () => {
  for (const phase of ["ending", "pausing"]) {
    const lifecycle = new CaptureLifecycle();
    const operation = lifecycle.begin(phase);
    lifecycle.finish(operation);
    assert.equal(captureStatus({ phase: lifecycle.phase, state: "capturing", hasInputs: false }), "Capture stopped · Pending processing");
  }
  assert.equal(captureStatus({ phase: "idle", state: "capturing", hasInputs: true }), "Listening");
});

test("processing and saved navigation states are independent from capture phase", () => {
  assert.equal(captureStatus({ phase: "idle", state: "finishing", hasInputs: false }), "Finishing checks…");
  assert.equal(captureStatus({ phase: "idle", state: "stopped", archived: true, incomplete: true }), "Checks incomplete");
  assert.equal(captureStatus({ phase: "idle", state: "stopped", archived: true, interrupted: true }), "Interrupted meeting");
  assert.equal(captureStatus({ phase: "idle", state: "stopped", archived: true }), "Saved meeting");
  assert.equal(captureStatus({ phase: "starting", state: "capturing", hasInputs: false }), "Starting…");
  assert.equal(captureStatus({ phase: "pausing", state: "capturing", hasInputs: false }), "Pausing…");
});

test("unknown transitions cannot change capture state", () => {
  const lifecycle = new CaptureLifecycle();
  assert.throws(() => lifecycle.begin("saved"), /Invalid capture transition/);
  assert.equal(lifecycle.phase, "idle");
});

test("external End preempts pending startup or pause and ignores their late completion", () => {
  for (const phase of ["starting", "pausing"]) {
    const lifecycle = new CaptureLifecycle();
    const previous = lifecycle.begin(phase);
    const ending = lifecycle.begin("ending");
    assert.ok(ending);
    assert.equal(lifecycle.owns(previous), false);
    assert.equal(lifecycle.finish(previous), false);
    assert.equal(lifecycle.phase, "ending");
    assert.equal(lifecycle.begin("starting"), undefined);
    lifecycle.finish(ending);
  }
});
