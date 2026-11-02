import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { AudioSpool } from "../audio-spool.mjs";
import { AudioSpoolController } from "../audio-spool-controller.mjs";
import { capturedChunk, UploadQueue } from "../meeting/upload-queue.mjs";

const sessionId = "12345678-1234-4234-8234-123456789abc";
const meta = (sequence = 0, source = "microphone") => ({ session_id: sessionId, epoch: 1, source, sequence, start_ms: sequence * 200, end_ms: sequence * 200 + 200, speech_end_ms: sequence * 200 + 190, age_ms: 0 });
const secure = { isEncryptionAvailable: () => true, encryptString: (value) => Buffer.from(value), decryptString: (value) => value.toString() };
const pcm = () => new Uint8Array(6400).fill(45).buffer;
test("hanging OS decryption is bounded, preserves ciphertext, and prevents late writes", async (context) => {
  const spool = await fixture(context, { secureTimeoutMs: 20 });
  await spool.append(meta(), pcm());
  const files = await fs.readdir(spool.directory);
  const before = await Promise.all(files.map(filename => fs.readFile(path.join(spool.directory, filename))));
  let resolveDecryption;
  const restored = new AudioSpool(spool.directory, { ...secure,
    isAsyncEncryptionAvailable: async () => true,
    decryptStringAsync: () => new Promise(resolve => { resolveDecryption = resolve; }),
  }, { secureTimeoutMs: 20 });
  await assert.rejects(restored.initialize(), /did not respond/);
  resolveDecryption({ result: Buffer.alloc(32).toString("base64") });
  await assert.rejects(restored.initialize(), /did not respond/);
  assert.equal(restored.initialized, undefined);
  assert.deepEqual(await Promise.all(files.map(filename => fs.readFile(path.join(spool.directory, filename)))), before);
});
test("local write retry retains capture-time freshness rather than expiring accepted audio", async (context) => {
  const spool = await fixture(context);
  let elapsedMs = 200;
  let unavailable = true;
  const queue = new UploadQueue(async (chunk) => {
    if (unavailable) throw Error("disk temporarily unavailable");
    return spool.append({ ...meta(), age_ms: chunk.age_ms }, chunk.pcm);
  }, () => {});
  queue.offer(capturedChunk({ ...meta(), pcm: pcm() }, elapsedMs));
  await assert.rejects(queue.finish(), /temporarily unavailable/);
  elapsedMs = 60000;
  unavailable = false;
  queue.retry();
  await queue.finish();
  assert.equal(spool.list().length, 1);
  assert.equal(spool.list()[0].meta.age_ms, 10);
  assert.equal(queue.pending.length, 0);
  assert.throws(() => capturedChunk({ ...meta(), pcm: pcm() }, elapsedMs), /too old/);
});
async function fixture(context, options = {}, storage = secure) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "grounded-spool-test-"));
  context.after(() => fs.rm(directory, { recursive: true, force: true }));
  return new AudioSpool(directory, storage, { reserveBytes: 0, ...options });
}

test("encrypted durable append is idempotent and restores without automatic upload", async (context) => {
  const spool = await fixture(context);
  const result = await spool.append(meta(), pcm());
  assert.equal(result.durable, true);
  const encrypted = await fs.readFile(path.join(spool.directory, `${result.id}.chunk`));
  assert.equal(encrypted.includes(Buffer.from(pcm())), false);
  assert.equal(encrypted.includes(Buffer.from(sessionId)), false);
  assert.equal((await spool.append(meta(), pcm())).duplicate, true);
  const restored = new AudioSpool(spool.directory, secure);
  await restored.initialize();
  assert.equal(restored.list().length, 1);
  assert.deepEqual((await restored.read(result.id)).bytes, pcm());
  assert.equal((await fs.stat(spool.directory)).mode & 0o777, 0o700);
  assert.equal((await fs.stat(path.join(spool.directory, `${result.id}.chunk`))).mode & 0o777, 0o600);
  const controller = new AudioSpoolController(restored, {});
  assert.equal(controller.status().meetings[0].authorized, false);
});

test("same identity with changed payload or timing is rejected", async (context) => {
  const spool = await fixture(context);
  await spool.append(meta(), pcm());
  await assert.rejects(spool.append(meta(), new Uint8Array(6400).fill(46).buffer), /conflict/);
  await assert.rejects(spool.append({ ...meta(), speech_end_ms: 180 }, pcm()), /conflict/);
  assert.equal(spool.list().length, 1);
});

test("quota and disk failure preserve existing chunks", async (context) => {
  const spool = await fixture(context, { maxBytes: 10000 });
  await spool.append(meta(), pcm());
  await assert.rejects(spool.append(meta(1), pcm()), /full/);
  assert.equal(spool.list().length, 1);
  spool.maxBytes = 100000;
  const original = spool.atomicWrite.bind(spool);
  spool.atomicWrite = async () => { throw Error("disk failure"); };
  await assert.rejects(spool.append(meta(1), pcm()), /disk failure/);
  spool.atomicWrite = original;
  assert.equal(spool.list().length, 1);
});

test("unavailable OS storage and insecure fallback fail closed", async (context) => {
  for (const storage of [{ ...secure, isEncryptionAvailable: () => false }, { ...secure, getSelectedStorageBackend: () => "basic_text" }]) {
    const spool = await fixture(context, {}, storage);
    await assert.rejects(spool.append(meta(), pcm()), /Secure OS/);
    assert.deepEqual(await fs.readdir(spool.directory), []);
  }
});

test("Electron asynchronous secure storage is preferred and failures never fall back to plaintext", async (context) => {
  const storage = {
    ...secure,
    isEncryptionAvailable: () => false,
    isAsyncEncryptionAvailable: async () => true,
    encryptStringAsync: async (value) => Buffer.from(value),
    decryptStringAsync: async (bytes) => ({ result: bytes.toString(), shouldReEncrypt: false }),
  };
  const spool = await fixture(context, {}, storage);
  await spool.append(meta(), pcm());
  const restored = new AudioSpool(spool.directory, storage);
  await restored.initialize();
  assert.equal(restored.list().length, 1);
  const denied = await fixture(context, {}, { ...storage, encryptStringAsync: async () => { throw Error("Keychain denied"); } });
  await assert.rejects(denied.append(meta(), pcm()), /Keychain denied/);
  assert.deepEqual(await fs.readdir(denied.directory), []);
});

test("corrupt ciphertext is retained and blocks new capture", async (context) => {
  const spool = await fixture(context);
  const { id } = await spool.append(meta(), pcm());
  const filename = path.join(spool.directory, `${id}.chunk`);
  const encrypted = await fs.readFile(filename);
  encrypted[encrypted.length - 1] ^= 1;
  await fs.writeFile(filename, encrypted);
  const restored = new AudioSpool(spool.directory, secure);
  await restored.initialize();
  assert.equal(restored.corrupt.size, 1);
  await assert.rejects(restored.append(meta(1), pcm()), /Unreadable/);
  assert.equal((await fs.stat(filename)).size, encrypted.length);
  await restored.quarantineCorrupt();
  assert.equal(restored.corrupt.size, 0);
  assert.equal(restored.retainedBytes, encrypted.length);
  const reloaded = new AudioSpool(spool.directory, secure, { reserveBytes: 0 });
  await reloaded.initialize();
  assert.equal(reloaded.quarantined, 1);
  assert.equal(reloaded.retainedBytes, encrypted.length);
  const isolated = (await fs.readdir(spool.directory)).find((name) => name.startsWith(".quarantine-"));
  assert.deepEqual(await fs.readFile(path.join(spool.directory, isolated)), encrypted);
  await reloaded.append(meta(1), pcm());
  reloaded.maxBytes = encrypted.length;
  await assert.rejects(reloaded.append(meta(2), pcm()), /full/);
});

test("failure after rename is recovered without duplicate ciphertext or false acknowledgement", async (context) => {
  const spool = await fixture(context);
  await spool.initialize();
  const original = spool.syncDirectory.bind(spool);
  spool.syncDirectory = async () => { throw Error("directory fsync failed"); };
  await assert.rejects(spool.append(meta(), pcm()), /fsync failed/);
  assert.equal((await fs.readdir(spool.directory)).filter((name) => name.endsWith(".chunk")).length, 1);
  spool.syncDirectory = original;
  assert.equal((await spool.append(meta(), pcm())).duplicate, true);
  const restored = new AudioSpool(spool.directory, secure);
  await restored.initialize();
  assert.deepEqual((await restored.read(spool.identity(meta()))).bytes, pcm());
});

test("crash residue is recovered when authenticated and retained when truncated", async (context) => {
  const spool = await fixture(context);
  await spool.initialize();
  const originalRename = spool.fs.rename;
  spool.fs = { ...fs, rename: async () => { throw Error("crash before rename"); } };
  await assert.rejects(spool.append(meta(), pcm()), /before rename/);
  const pending = (await fs.readdir(spool.directory)).filter((name) => name.startsWith(".pending-"));
  assert.equal(pending.length, 1);
  await fs.writeFile(path.join(spool.directory, ".pending-truncated"), Buffer.from("GAS1broken"), { mode: 0o600 });
  spool.fs = { ...fs, rename: originalRename };
  const restored = new AudioSpool(spool.directory, secure);
  await restored.initialize();
  assert.equal(restored.list().length, 1);
  assert.equal(restored.corrupt.size, 1);
  assert.deepEqual((await restored.read(spool.identity(meta()))).bytes, pcm());
  assert.equal((await fs.readFile(path.join(spool.directory, ".pending-truncated"))).toString(), "GAS1broken");
  await assert.rejects(restored.append(meta(1), pcm()), /Unreadable/);
});

test("a terminated writer leaves an authenticated pending file recoverable by a new process", async (context) => {
  const spool = await fixture(context);
  await spool.initialize();
  const moduleUrl = new URL("../audio-spool.mjs", import.meta.url).href;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", `
    import fs from "node:fs/promises";
    import { AudioSpool } from ${JSON.stringify(moduleUrl)};
    const secure = { isEncryptionAvailable: () => true, encryptString: value => Buffer.from(value), decryptString: value => value.toString() };
    const spool = new AudioSpool(${JSON.stringify(spool.directory)}, secure, { reserveBytes: 0, fs: { ...fs, rename: async () => process.exit(73) } });
    await spool.append(${JSON.stringify(meta())}, new Uint8Array(6400).fill(45).buffer);
  `]);
  assert.equal(result.status, 73);
  assert.equal((await fs.readdir(spool.directory)).filter(name => name.startsWith(".pending-")).length, 1);
  const restored = new AudioSpool(spool.directory, secure);
  await restored.initialize();
  assert.equal(restored.corrupt.size, 0);
  assert.equal(restored.list().length, 1);
  assert.deepEqual((await restored.read(spool.identity(meta()))).bytes, pcm());
});

test("accepted upload is retained until committed receipt, ordered across sources", async (context) => {
  const spool = await fixture(context);
  await spool.append(meta(), pcm());
  await spool.append(meta(1), pcm());
  await spool.append(meta(0, "system"), pcm());
  const states = new Map();
  const uploaded = [];
  const client = {
    audioStatus: async (metadata) => {
      const key = metadata.source + metadata.sequence;
      if (states.get(key) === "pending") {
        assert.ok(spool.list().some((entry) => entry.meta.source + entry.meta.sequence === key));
        states.set(key, "processed");
      }
      return { status: states.get(key) ?? "missing" };
    },
    audio: async (metadata) => { uploaded.push(metadata.source + metadata.sequence); states.set(metadata.source + metadata.sequence, "pending"); return { accepted: true }; },
    abort() {},
  };
  const controller = new AudioSpoolController(spool, client, { sleep: async () => {}, pollMs: 0 });
  await controller.authorize(sessionId);
  await controller.running;
  assert.deepEqual(uploaded, ["microphone0", "system0", "microphone1"]);
  assert.equal(spool.list().length, 0);
});

test("lost response after commit queries same identity without retranscribing", async (context) => {
  const spool = await fixture(context);
  await spool.append(meta(), pcm());
  let committed = false, uploads = 0;
  const client = {
    audioStatus: async () => ({ status: committed ? "processed" : "missing" }),
    audio: async () => { uploads++; committed = true; throw Error("network timeout"); },
    abort() {},
  };
  const controller = new AudioSpoolController(spool, client, { sleep: async () => {} });
  await controller.authorize(sessionId);
  await controller.running;
  assert.equal(uploads, 1);
  assert.equal(spool.list().length, 0);
});

test("network failures exhaust bounded retries without deleting audio", async (context) => {
  const spool = await fixture(context);
  await spool.append(meta(), pcm());
  let attempts = 0;
  const client = { audioStatus: async () => { attempts++; throw Error("offline"); }, abort() {} };
  const controller = new AudioSpoolController(spool, client, { sleep: async () => {}, maxAttempts: 3 });
  await controller.authorize(sessionId);
  await controller.running;
  assert.equal(attempts, 3);
  assert.equal(spool.list().length, 1);
  assert.equal(controller.status().failed, 1);
  await assert.rejects(controller.finish(sessionId), /retained|encrypted/);
});

test("explicit cancellation contacts server before local deletion", async (context) => {
  const spool = await fixture(context);
  await spool.append(meta(), pcm());
  const client = { abort() {}, cancelProcessing: async () => { assert.equal(spool.list().length, 1); } };
  const controller = new AudioSpoolController(spool, client);
  await controller.discard(sessionId);
  assert.equal(spool.list().length, 0);
});

test("processing failure does not count as a durable deletion acknowledgement", async (context) => {
  const spool = await fixture(context);
  await spool.append(meta(), pcm());
  const client = { audioStatus: async () => ({ status: "failed", retryable: false }), abort() {} };
  const controller = new AudioSpoolController(spool, client);
  await controller.authorize(sessionId);
  await controller.running;
  assert.equal(spool.list().length, 1);
});

for (const rejection of ["admission", "receipt"]) {
  test(`permanent capacity ${rejection} retains audio without repeating requests or promising recovery`, async (context) => {
    const spool = await fixture(context);
    await spool.append(meta(), pcm());
    let requests = 0;
    const client = {
      audioStatus: async () => {
        requests++;
        return rejection === "receipt" ? { status: "failed", retryable: false, code: "AUDIO_CAPACITY_REACHED" } : { status: "missing" };
      },
      audio: async () => { throw Error("[AUDIO_CAPACITY_REACHED] limit reached"); },
      abort() {},
    };
    const controller = new AudioSpoolController(spool, client, { sleep: async () => {} });
    await controller.authorize(sessionId);
    await controller.running;
    assert.equal(requests, 1);
    assert.equal(spool.list().length, 1);
    assert.equal(controller.status().meetings[0].retryable, false);
    await assert.rejects(controller.finish(sessionId), /retrying cannot raise/);
    await assert.rejects(controller.authorize(sessionId, true), /retrying cannot raise/);
    assert.equal(requests, 1);
  });
}
