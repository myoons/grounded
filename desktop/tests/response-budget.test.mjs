import test from "node:test";
import assert from "node:assert/strict";
import { MeetingClient, MAX_MEETING_RESPONSE_BYTES } from "../meeting-client.mjs";

test("bounded history responses can exceed the former two-million-character cap", async () => {
  const payload = { text: "a".repeat(2_100_000) };
  const client = new MeetingClient("http://127.0.0.1:8877", async () => new Response(JSON.stringify(payload)));
  assert.deepEqual(await client.command({ type: "capabilities" }), payload);
  assert.equal(client.pending.size, 0);
});

test("oversize streamed responses are cancelled by byte count without trusting headers", async () => {
  let cancelled = false;
  let blocks = 0;
  const body = new ReadableStream({
    pull(controller) { blocks++; controller.enqueue(new Uint8Array(1024 * 1024)); },
    cancel() { cancelled = true; },
  });
  const client = new MeetingClient("http://127.0.0.1:8877", async () => new Response(body, { headers: { "Content-Length": "1" } }));
  await assert.rejects(client.command({ type: "capabilities" }), /exceeds the size limit/);
  assert.equal(cancelled, true);
  assert.ok(blocks <= MAX_MEETING_RESPONSE_BYTES / (1024 * 1024) + 2);
  assert.equal(client.pending.size, 0);
});

test("split UTF-8 sequences decode correctly and malformed JSON is rejected", async () => {
  const bytes = new TextEncoder().encode(JSON.stringify({ text: "café" }));
  const body = new ReadableStream({ start(controller) {
    for (const value of bytes) controller.enqueue(Uint8Array.of(value));
    controller.close();
  } });
  const client = new MeetingClient("http://127.0.0.1:8877", async () => new Response(body));
  assert.deepEqual(await client.command({ type: "capabilities" }), { text: "café" });
  const malformed = new MeetingClient("http://127.0.0.1:8877", async () => new Response("not json"));
  await assert.rejects(malformed.command({ type: "capabilities" }), /Invalid meeting service response/);
});
