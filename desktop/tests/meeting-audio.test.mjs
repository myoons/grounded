import test from "node:test";
import assert from "node:assert/strict";
import { UtteranceChunker } from "../meeting/chunker.mjs";
import { UploadQueue } from "../meeting/upload-queue.mjs";
import {
  allowsMeetingPermission,
  trustedMeetingFrame,
} from "../meeting-permissions.mjs";
import { MeetingClient } from "../meeting-client.mjs";

const tone = () => new Float32Array(3200).fill(0.1);
const silence = () => new Float32Array(3200);

test("continuous audio splits on silence and preserves pre-roll and 1200 ms overlap", () => {
  const chunks = [],
    chunker = new UtteranceChunker((chunk) => chunks.push(chunk), 1000);
  chunker.push(silence());
  chunker.push(silence());
  for (let block = 0; block < 35; block++) chunker.push(tone());
  for (let block = 0; block < 3; block++) chunker.push(silence());
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].end_ms - chunks[1].start_ms, 1200);
  assert.equal(chunks[0].sequence, 0);
  assert.equal(chunks[1].sequence, 1);
  assert.ok(chunks.every((chunk) => chunk.pcm.byteLength <= 224000));
  assert.ok(
    chunks.every(
      (chunk) => chunk.pcm.byteLength / 32 === chunk.end_ms - chunk.start_ms,
    ),
  );
  assert.equal(chunks[1].end_ms - chunks[1].speech_end_ms, 600);
});

test("silence is not uploaded and discarding a partial utterance cannot leak it later", () => {
  const chunks = [],
    chunker = new UtteranceChunker((chunk) => chunks.push(chunk));
  for (let block = 0; block < 1000; block++) chunker.push(silence());
  assert.equal(chunks.length, 0);
  assert.ok(chunker.preRoll.length <= 2);
  chunker.push(tone());
  chunker.discard();
  for (let block = 0; block < 5; block++) chunker.push(silence());
  assert.equal(chunks.length, 0);
  assert.throws(() => chunker.push(new Float32Array(1)));
});

test("an overlap-only tail is not retranscribed but new short speech is preserved", () => {
  const chunks = [];
  const chunker = new UtteranceChunker((chunk) => chunks.push(chunk));
  for (let block = 0; block < 29; block++) chunker.push(tone());
  for (let block = 0; block < 8; block++) chunker.push(silence());
  assert.equal(chunks.length, 1);
  chunker.push(tone());
  for (let block = 0; block < 3; block++) chunker.push(silence());
  assert.equal(chunks.length, 2);
  assert.equal(chunks[1].sequence, 1);
  assert.ok(chunks[1].speech_end_ms > chunks[0].speech_end_ms);
});

test("local append queue rejects new intake under backpressure but drains accepted writes", async () => {
  let release;
  const failures = [],
    sent = [];
  const queue = new UploadQueue(
    (chunk) => {
      sent.push(chunk);
      return sent.length === 1 ? new Promise((resolve) => {
        release = resolve;
      }) : Promise.resolve();
    },
    (message) => failures.push(message),
  );
  for (let index = 0; index < 33; index++) queue.offer({ sequence: index });
  assert.equal(queue.stopped, true);
  assert.equal(failures.length, 1);
  assert.equal(queue.pending.length, 32);
  release();
  await queue.finish();
  assert.equal(sent.length, 32);
  assert.equal(queue.pending.length, 0);
});

test("local append failure retains failed chunk and explicit retry preserves order", async () => {
  let fail = true;
  const saved = [];
  const queue = new UploadQueue(async (chunk) => {
    if (fail) throw Error("disk full");
    saved.push(chunk.sequence);
  }, () => {}, () => 100000);
  queue.offer({ sequence: 0 });
  queue.offer({ sequence: 1 });
  await assert.rejects(queue.finish(), /disk full/);
  assert.equal(queue.pending.length, 2);
  fail = false;
  queue.retry();
  await queue.finish();
  assert.deepEqual(saved, [0, 1]);
});

test("meeting permission policy excludes camera, foreign origins and subframes", () => {
  const contents = {},
    origin = "http://127.0.0.1:8880";
  assert.equal(
    trustedMeetingFrame(contents, contents, origin, origin, {
      isMainFrame: true,
      requestingUrl: origin,
    }),
    true,
  );
  assert.equal(
    trustedMeetingFrame(contents, contents, origin, "https://example.com", {
      isMainFrame: true,
      requestingUrl: origin,
    }),
    false,
  );
  assert.equal(
    trustedMeetingFrame(contents, contents, origin, origin, {
      isMainFrame: false,
      requestingUrl: origin,
    }),
    false,
  );
  assert.equal(
    allowsMeetingPermission("media", { mediaTypes: ["video"] }),
    false,
  );
  assert.equal(
    allowsMeetingPermission("media", { mediaTypes: ["audio"] }),
    true,
  );
  assert.equal(allowsMeetingPermission("display-capture", {}), false);
});

test("meeting bridge restricts destinations and bounds PCM metadata", async () => {
  assert.throws(() => new MeetingClient("https://example.com"));
  const seen = [],
    client = new MeetingClient(
      "http://127.0.0.1:8877",
      async (url, options) => {
        seen.push({ url, options });
        return new Response(JSON.stringify({ accepted: true }));
      },
    );
  await assert.rejects(() =>
    client.command({ type: "state", session_id: "../../secrets" }),
  );
  await assert.rejects(() => client.command({ type: "start", consent: false }));
  await assert.rejects(() => client.command({ type: "start", consent: true, language: "en", cloud_detection: false }));
  const meta = {
    session_id: "00000000-0000-4000-8000-000000000001",
    source: "system",
    epoch: 1,
    sequence: 0,
    start_ms: 0,
    end_ms: 1000,
    speech_end_ms: 900,
    age_ms: 100,
  };
  await client.audio(meta, new ArrayBuffer(32000));
  assert.equal(seen[0].options.headers["X-Audio-Source"], "system");
  assert.equal(seen[0].options.redirect, "error");
  await assert.rejects(() => client.audio(meta, new ArrayBuffer(300000)));
});
