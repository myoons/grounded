import assert from "node:assert/strict";
import test from "node:test";
import { EventEmitter } from "node:events";
import { PCMFrames, NativeAudio } from "../native-audio.mjs";
import { NativeAudioInput } from "../meeting/native-audio-input.mjs";

const streamId = "00000000-0000-4000-8000-000000000001";
const pcm = () => { const bytes = Buffer.alloc(12800); for (let index = 0; index < 3200; index++) bytes.writeFloatLE(0.25, index * 4); return bytes; };
function childProcess() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.exitCode = null; child.signalCode = null;
  child.kill = (signal) => { child.signalCode = signal; queueMicrotask(() => child.emit("exit", null, signal)); };
  return child;
}

test("native PCM framing handles split bytes and validates samples", () => {
  const received = [];
  const frames = new PCMFrames((samples) => received.push(samples));
  const bytes = pcm();
  frames.push(bytes.subarray(0, 3)); frames.push(bytes.subarray(3));
  assert.equal(received.length, 1);
  assert.equal(received[0].length, 3200);
  assert.equal(received[0][17], 0.25);
  const invalid = pcm(); invalid.writeFloatLE(NaN, 4);
  assert.throws(() => frames.push(invalid));
});

test("native capture stops on unacknowledged renderer backlog and rejects stale starts", async () => {
  const child = childProcess(); const events = [];
  const native = new NativeAudio("test", (event) => events.push(event), () => child);
  const start = native.start(streamId);
  child.stdout.emit("data", pcm()); await start;
  await assert.rejects(native.start(streamId));
  native.acknowledge("wrong-stream", 0);
  assert.equal(native.active.outstanding.size, 1);
  native.acknowledge(streamId, 0);
  assert.equal(native.active.outstanding.size, 0);
  for (let index = 0; index < 9; index++) child.stdout.emit("data", pcm());
  assert.equal(events.at(-1).code, "NATIVE_AUDIO_BACKPRESSURE");
  assert.equal(native.active, undefined);
  await native.stop();
});

test("native start can be cancelled before the first samples without replay", async () => {
  const child = childProcess(); const events = [];
  const native = new NativeAudio("test", (event) => events.push(event), () => child);
  const pending = native.start(streamId);
  const rejected = assert.rejects(pending, /stopped/);
  await native.stop(streamId); await rejected;
  child.stdout.emit("data", pcm());
  assert.equal(events.length, 0);
});

test("native input ignores other epochs and feeds the existing speech chunker", async () => {
  let listener; const chunks = []; const states = []; const commands = [];
  const bridge = { onSystemAudio(callback) { listener = callback; return () => { listener = undefined; }; }, async command(command) { commands.push(command); return {}; } };
  const input = new NativeAudioInput(performance.now(), (chunk) => chunks.push(chunk), (state) => states.push(state), bridge);
  await input.start();
  listener({ streamId: "old", sequence: 0, samples: new Float32Array(3200).fill(0.2) });
  for (let index = 0; index < 31; index++) listener({ streamId: input.streamId, sequence: index, samples: new Float32Array(3200).fill(0.2) });
  assert.equal(chunks.length, 1); assert.equal(chunks[0].source, "system");
  assert.equal(states.at(-1).state, "signal");
  await input.stop();
  assert.equal(listener, undefined);
  assert.equal(commands.at(-1).type, "native_audio_stop");
});

test("graceful native stop drains stdout through close, including its partial last frame", async () => {
  const child = childProcess();
  const events = [];
  const native = new NativeAudio("test", (event) => events.push(event), () => child);
  const started = native.start(streamId);
  child.stdout.emit("data", pcm());
  await started;
  child.kill = (signal) => queueMicrotask(() => {
    child.emit("exit", 0, signal);
    child.stdout.emit("data", pcm().subarray(0, 400));
    child.emit("close", 0, signal);
  });
  await Promise.all([native.stop(streamId, true), native.stop(streamId, true)]);
  assert.equal(events.length, 2);
  assert.equal(events[1].samples[99], 0.25);
  assert.equal(events[1].samples[100], 0);
  assert.equal(events.some((event) => event.state === "error"), false);
  assert.equal(native.active, undefined);
});
