import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { AudioSpool } from "../audio-spool.mjs";
import { AudioSpoolController } from "../audio-spool-controller.mjs";
import { MeetingClient } from "../meeting-client.mjs";

test("production desktop client and server persist receipt across restart and ambiguous upload", { timeout: 30000 }, async (context) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "grounded-durable-http-"));
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const server = spawn(path.join(root, ".venv/bin/python"), ["-m", "tests.durable_audio_fixture", directory], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  server.stderr.resume();
  context.after(async () => {
    if (server.exitCode === null) { server.kill("SIGTERM"); await once(server, "exit"); }
    await fs.rm(directory, { recursive: true, force: true });
  });
  let output = "";
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Synthetic server did not start")), 15000);
    server.on("error", reject);
    server.stdout.on("data", (bytes) => {
      output += bytes;
      if (output.includes("\n")) { clearTimeout(timer); resolve(JSON.parse(output.split("\n")[0]).port); }
    });
  });
  const origin = `http://127.0.0.1:${port}`;
  let loseResponse = true;
  const client = new MeetingClient(origin, async (url, options) => {
    const response = await fetch(url, options);
    if (loseResponse && options.method === "POST" && url.endsWith("/audio") && response.ok) {
      loseResponse = false;
      throw Error("Synthetic response loss after admission");
    }
    return response;
  });
  for (let attempt = 0; attempt < 100; attempt++) {
    try { await client.command({ type: "capabilities" }); break; }
    catch { await new Promise((resolve) => setTimeout(resolve, 25)); }
  }
  const meeting = await client.command({ type: "start", consent: true, audio_consent_version: "runpod-audio-v2", cloud_detection: false, language: "en" });
  const metadata = { session_id: meeting.id, epoch: meeting.epoch, source: "microphone", sequence: 0, start_ms: 0, end_ms: 200, speech_end_ms: 190, age_ms: 0 };
  const secure = { isEncryptionAvailable: () => true, encryptString: (value) => Buffer.from(value), decryptString: (value) => value.toString() };
  const location = path.join(directory, "spool");
  const spool = new AudioSpool(location, secure, { reserveBytes: 0 });
  await spool.append(metadata, new ArrayBuffer(6400));
  await client.request("/__synthetic_restart", "POST");
  const restored = new AudioSpool(location, secure, { reserveBytes: 0 });
  const controller = new AudioSpoolController(restored, client, { pollMs: 5, sleep: (milliseconds) => new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 5))) });
  await controller.ready();
  assert.equal(controller.status().pending, 1);
  assert.equal(controller.status().meetings[0].authorized, false);
  await controller.authorize(meeting.id, true);
  await controller.finish(meeting.id, 10000);
  assert.equal(restored.list().length, 0);
  const snapshot = await client.command({ type: "state", session_id: meeting.id });
  assert.equal(snapshot.segments.length, 1);
  assert.equal(snapshot.segments[0].text, "Synthetic speech");
  await client.request("/__synthetic_restart", "POST");
  await client.resumeProcessing(meeting.id);
  await client.audio({ ...metadata, durable: true }, new ArrayBuffer(6400));
  assert.equal((await client.command({ type: "state", session_id: meeting.id })).segments.length, 1);
  controller.close();
  await client.cancelProcessing(meeting.id);
  await client.command({ type: "clear", session_id: meeting.id });
  const pendingMeeting = await client.command({ type: "start", consent: true, audio_consent_version: "runpod-audio-v2", cloud_detection: false, language: "en" });
  assert.deepEqual(await client.request("/__synthetic_unextracted", "POST"), { segments: 1, candidates: 0 });
  assert.equal(restored.list().length, 0);
  await client.request("/__synthetic_restart", "POST");
  const recovery = new AudioSpoolController(restored, client);
  await recovery.authorize(pendingMeeting.id, true);
  assert.deepEqual(await recovery.finish(pendingMeeting.id), { drained: true, pending: 0 });
  await client.command({ type: "action", session_id: pendingMeeting.id, action: "stop" });
  let final;
  for (let attempt = 0; attempt < 100; attempt++) {
    final = await client.command({ type: "state", session_id: pendingMeeting.id });
    if (final.finalization.state === "complete") break;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.equal(final.finalization.state, "complete");
  assert.equal(final.segments.length, 1);
  assert.equal(final.candidates.length, 1);
  assert.equal(final.candidates[0].research.state, "ready");
  assert.equal(restored.list().length, 0);
  recovery.close();
});
