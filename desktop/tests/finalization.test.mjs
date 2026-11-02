import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { UploadQueue } from "../meeting/upload-queue.mjs";
import { UtteranceChunker } from "../meeting/chunker.mjs";
import { NativeAudioInput } from "../meeting/native-audio-input.mjs";
import { AudioInput } from "../meeting/audio-input.mjs";
import { PCMFrames } from "../native-audio.mjs";
import { MeetingClient } from "../meeting-client.mjs";

test("final utterance uploads once before drain completion without trailing silence", async () => {
  const sent = [];
  let release;
  const queue = new UploadQueue(async (chunk) => {
    sent.push(chunk);
    await new Promise((resolve) => { release = resolve; });
  }, () => assert.fail("Unexpected failure"));
  const chunker = new UtteranceChunker((chunk) => queue.offer(chunk));
  chunker.push(new Float32Array(3200).fill(0.1));
  chunker.flush();
  chunker.flush();
  let completed = false;
  const finished = queue.finish().then(() => { completed = true; });
  await Promise.resolve();
  assert.equal(completed, false);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].speech_end_ms, 200);
  release();
  await finished;
  assert.equal(completed, true);
});

test("drain sends accepted chunks in order and exposes failures and bounded timeout", async () => {
  const sent = [];
  const queue = new UploadQueue(async (chunk) => { sent.push(chunk.sequence); }, () => {});
  queue.offer({ sequence: 0 });
  queue.offer({ sequence: 1 });
  await queue.finish();
  assert.deepEqual(sent, [0, 1]);
  const failed = new UploadQueue(async () => { throw Error("offline"); }, () => {});
  failed.offer({});
  await assert.rejects(failed.finish(), /offline/);
  const hung = new UploadQueue(() => new Promise(() => {}), () => {});
  hung.offer({});
  await assert.rejects(hung.finish(5), /not been saved locally/);
  assert.equal(hung.pending.length, 1);
});

test("native partial frame is padded once and empty flush emits nothing", () => {
  const emitted = [];
  const frames = new PCMFrames((frame) => emitted.push(frame));
  const bytes = Buffer.alloc(400);
  for (let index = 0; index < 100; index++) bytes.writeFloatLE(0.5, index * 4);
  frames.push(bytes);
  frames.flush();
  frames.flush();
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0][99], 0.5);
  assert.equal(emitted[0][100], 0);
});

test("worklet flush preserves sub-block audio and acknowledges after final samples", async () => {
  let Processor;
  class Base { constructor() { this.port = { postMessage: (value) => messages.push(value) }; } }
  const messages = [];
  vm.runInNewContext(await readFile(new URL("../meeting/pcm-worklet.js", import.meta.url), "utf8"), {
    AudioWorkletProcessor: Base, Float32Array,
    registerProcessor: (_name, value) => { Processor = value; },
  });
  const processor = new Processor();
  processor.process([[new Float32Array(128).fill(0.5)]]);
  processor.port.onmessage({ data: "flush" });
  assert.equal(messages[0][127], 0.5);
  assert.equal(messages[0][128], 0);
  assert.equal(messages[1], "flushed");
  processor.port.onmessage({ data: "flush" });
  assert.equal(messages.length, 3);
});

test("native stop receives final samples before unsubscribing and repeated stop is idempotent", async () => {
  let listener;
  const chunks = [], commands = [];
  const input = new NativeAudioInput(0, (chunk) => chunks.push(chunk), () => {}, {
    onSystemAudio(callback) { listener = callback; return () => { listener = null; }; },
    async command(command) {
      commands.push(command);
      if (command.flush) listener({ streamId: input.streamId, sequence: 1, samples: new Float32Array(3200).fill(0.1) });
    },
  });
  await input.start();
  listener({ streamId: input.streamId, sequence: 0, samples: new Float32Array(3200).fill(0.1) });
  await Promise.all([input.stop({ flush: true }), input.stop({ flush: true })]);
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].pcm.byteLength, 12800);
  assert.equal(commands.filter((command) => command.flush).length, 1);
  assert.equal(listener, null);
});

test("microphone graceful stop flushes but ordinary interruption discards", async () => {
  for (const flush of [true, false]) {
    const chunks = [];
    const input = new AudioInput("microphone", 0, () => {}, () => {}, {});
    input.chunker = new UtteranceChunker((chunk) => chunks.push(chunk));
    input.chunker.push(new Float32Array(3200).fill(0.1));
    await Promise.all([input.stop({ flush }), input.stop({ flush })]);
    assert.equal(chunks.length, flush ? 1 : 0);
  }
});

test("client forwards capture loss to finalization, not review commands", async () => {
  let body;
  const client = new MeetingClient("http://127.0.0.1:8877", async (_url, options) => {
    body = JSON.parse(options.body);
    return new Response("{}");
  });
  await client.command({ type: "action", session_id: "00000000-0000-4000-8000-000000000001", action: "stop", finalization_error: "Audio upload failed." });
  assert.deepEqual(body, { action: "stop", finalization_error: "Audio upload failed." });
});
