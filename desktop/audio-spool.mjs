import fs from "node:fs/promises";
import path from "node:path";
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");

export function validateAudio(meta, bytes) {
  if (!meta || !uuid.test(meta.session_id) || !["microphone", "system"].includes(meta.source)
    || !Number.isSafeInteger(meta.epoch) || meta.epoch < 0 || !Number.isSafeInteger(meta.sequence) || meta.sequence < 0
    || ![meta.start_ms, meta.end_ms, meta.speech_end_ms, meta.age_ms].every(Number.isFinite)
    || meta.start_ms < 0 || meta.end_ms <= meta.start_ms || meta.end_ms > 3_600_000
    || meta.speech_end_ms < meta.start_ms || meta.speech_end_ms > meta.end_ms || meta.age_ms < 0 || meta.age_ms > 15000
    || !(bytes instanceof ArrayBuffer) || bytes.byteLength < 3200 || bytes.byteLength > 224000 || bytes.byteLength % 2
    || Math.abs(meta.end_ms - meta.start_ms - bytes.byteLength / 32) > 2) throw Error("Invalid captured audio chunk.");
}

export class AudioSpool {
  constructor(directory, secureStorage, options = {}) {
    this.directory = directory;
    this.secureStorage = secureStorage;
    this.fs = options.fs ?? fs;
    this.maxBytes = options.maxBytes ?? 256 * 1024 * 1024;
    this.reserveBytes = options.reserveBytes ?? 128 * 1024 * 1024;
    this.maxDurationMs = options.maxDurationMs ?? 4 * 60 * 60 * 1000;
    this.entries = new Map();
    this.corrupt = new Set();
    this.retainedBytes = 0;
    this.quarantined = 0;
    this.serial = Promise.resolve();
    this.secureTimeoutMs = options.secureTimeoutMs ?? 10000;
  }

  async secureOperation(operation) {
    if (this.secureFailure) throw this.secureFailure;
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(operation),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            this.secureFailure = Error("Secure OS key storage did not respond. Capture is disabled and existing audio is preserved. Check Keychain access, then restart Grounded.");
            reject(this.secureFailure);
          }, this.secureTimeoutMs);
        }),
      ]);
    } finally { clearTimeout(timer); }
  }

  exclusive(operation) {
    const result = this.serial.then(operation);
    this.serial = result.catch(() => {});
    return result;
  }

  async syncDirectory() {
    const directory = await this.fs.open(this.directory, "r");
    try { await directory.sync(); } finally { await directory.close(); }
  }

  async atomicWrite(filename, bytes) {
    const temporary = path.join(this.directory, `.pending-${randomBytes(12).toString("hex")}`);
    let file;
    try {
      file = await this.fs.open(temporary, "wx", 0o600);
      await file.writeFile(bytes);
      await file.sync();
      await file.close();
      file = undefined;
      await this.fs.rename(temporary, path.join(this.directory, filename));
      await this.syncDirectory();
    } finally {
      await file?.close();
    }
  }

  async initialize() {
    if (this.initialized) return;
    this.entries.clear();
    this.corrupt.clear();
    this.retainedBytes = 0;
    this.quarantined = 0;
    const available = this.secureStorage.isAsyncEncryptionAvailable
      ? await this.secureOperation(() => this.secureStorage.isAsyncEncryptionAvailable())
      : this.secureStorage.isEncryptionAvailable();
    if (!available
      || this.secureStorage.getSelectedStorageBackend?.() === "basic_text") {
      throw Error("Secure OS key storage is unavailable. Capture is disabled; no plaintext audio is saved.");
    }
    await this.fs.mkdir(this.directory, { recursive: true, mode: 0o700 });
    await this.fs.chmod(this.directory, 0o700);
    const files = await this.fs.readdir(this.directory);
    const keyPath = path.join(this.directory, "key.enc");
    let wrapped;
    try { wrapped = await this.fs.readFile(keyPath); }
    catch (error) {
      if (error.code !== "ENOENT") throw error;
      if (files.some((filename) => filename.endsWith(".chunk") || filename.startsWith(".pending-") || filename.startsWith(".quarantine-"))) throw Error("The audio encryption key is missing. Existing audio was preserved; capture is disabled.");
      const generated = randomBytes(32).toString("base64");
      wrapped = this.secureStorage.encryptStringAsync
        ? await this.secureOperation(() => this.secureStorage.encryptStringAsync(generated))
        : this.secureStorage.encryptString(generated);
      await this.atomicWrite("key.enc", wrapped);
    }
    const unwrapped = this.secureStorage.decryptStringAsync
      ? (await this.secureOperation(() => this.secureStorage.decryptStringAsync(wrapped))).result
      : this.secureStorage.decryptString(wrapped);
    this.key = Buffer.from(unwrapped, "base64");
    if (this.key.length !== 32) throw Error("The audio encryption key could not be opened.");
    for (const filename of files.filter((name) => name.startsWith(".pending-"))) {
      try {
        const bytes = await this.fs.readFile(path.join(this.directory, filename));
        const entry = this.decode(bytes);
        const destination = `${this.identity(entry.meta)}.chunk`;
        if (files.includes(destination)) throw Error("Recovery identity already exists");
        await this.fs.rename(path.join(this.directory, filename), path.join(this.directory, destination));
        await this.syncDirectory();
        files.push(destination);
      } catch {
        if (await this.fs.stat(path.join(this.directory, filename)).catch(() => null)) this.corrupt.add(filename);
      }
    }
    for (const filename of files.filter((name) => name.startsWith(".quarantine-"))) {
      this.retainedBytes += (await this.fs.stat(path.join(this.directory, filename))).size;
      this.quarantined++;
    }
    for (const filename of (await this.fs.readdir(this.directory)).filter((name) => /^[a-f0-9]{64}\.chunk$/.test(name))) {
      try {
        const bytes = await this.fs.readFile(path.join(this.directory, filename));
        const entry = this.decode(bytes);
        const id = this.identity(entry.meta);
        if (filename !== `${id}.chunk`) throw Error("Invalid chunk identity");
        this.entries.set(id, { meta: entry.meta, digest: entry.digest, storedBytes: bytes.length, createdAt: entry.createdAt });
      } catch { this.corrupt.add(filename); }
    }
    this.initialized = true;
  }

  quarantineCorrupt() {
    return this.exclusive(async () => {
      await this.initialize();
      for (const filename of [...this.corrupt]) {
        const source = path.join(this.directory, filename);
        const size = (await this.fs.stat(source)).size;
        await this.fs.rename(source, path.join(this.directory, `.quarantine-${randomBytes(12).toString("hex")}`));
        this.corrupt.delete(filename);
        this.entries.delete(filename.replace(/\.chunk$/, ""));
        this.retainedBytes += size;
        this.quarantined++;
        await this.syncDirectory();
      }
      return { quarantined: this.quarantined };
    });
  }

  identity(meta) {
    return hash(`${meta.session_id}:${meta.epoch}:${meta.source}:${meta.sequence}`);
  }

  encode(entry) {
    const nonce = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key, nonce);
    cipher.setAAD(Buffer.from("grounded-audio-spool-v1"));
    const encrypted = Buffer.concat([cipher.update(JSON.stringify(entry)), cipher.final()]);
    return Buffer.concat([Buffer.from("GAS1"), nonce, cipher.getAuthTag(), encrypted]);
  }

  decode(bytes) {
    if (bytes.subarray(0, 4).toString() !== "GAS1") throw Error("Unsupported audio spool format");
    const decipher = createDecipheriv("aes-256-gcm", this.key, bytes.subarray(4, 16));
    decipher.setAAD(Buffer.from("grounded-audio-spool-v1"));
    decipher.setAuthTag(bytes.subarray(16, 32));
    const entry = JSON.parse(Buffer.concat([decipher.update(bytes.subarray(32)), decipher.final()]).toString());
    const pcm = Buffer.from(entry.pcm, "base64");
    const array = pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength);
    validateAudio(entry.meta, array);
    if (hash(pcm) !== entry.digest) throw Error("Audio integrity check failed");
    return { ...entry, bytes: array };
  }

  append(meta, bytes) {
    return this.exclusive(async () => {
      validateAudio(meta, bytes);
      await this.initialize();
      if (this.corrupt.size) throw Error("Unreadable encrypted audio is preserved. Resolve recovery before capturing more audio.");
      const id = this.identity(meta);
      const digest = hash(Buffer.from(bytes));
      const existing = this.entries.get(id);
      if (existing) {
        if (existing.digest !== digest || ["start_ms", "end_ms", "speech_end_ms"].some((field) => existing.meta[field] !== meta[field])) throw Error("Audio identity conflict.");
        await this.syncDirectory();
        return { durable: true, id, duplicate: true };
      }
      const createdAt = Date.now();
      const encrypted = this.encode({ meta, digest, createdAt, pcm: Buffer.from(bytes).toString("base64") });
      const entries = [...this.entries.values()];
      const total = this.retainedBytes + entries.reduce((sum, entry) => sum + entry.storedBytes, 0);
      const duration = entries.reduce((sum, entry) => sum + entry.meta.end_ms - entry.meta.start_ms, 0);
      const capacity = await this.fs.statfs(this.directory);
      if (total + encrypted.length > this.maxBytes || duration + meta.end_ms - meta.start_ms > this.maxDurationMs
        || capacity.bavail * capacity.bsize < this.reserveBytes + encrypted.length) {
        throw Error("Encrypted audio storage is full. Existing audio is preserved; new capture must stop.");
      }
      try { await this.atomicWrite(`${id}.chunk`, encrypted); }
      catch (error) {
        this.initialized = false;
        throw error;
      }
      this.entries.set(id, { meta: { ...meta }, digest, storedBytes: encrypted.length, createdAt });
      return { durable: true, id, duplicate: false };
    });
  }

  async read(id) {
    if (!this.entries.has(id)) throw Error("Unknown pending audio.");
    try { return this.decode(await this.fs.readFile(path.join(this.directory, `${id}.chunk`))); }
    catch { this.corrupt.add(`${id}.chunk`); throw Error("Encrypted audio could not be read. It has been preserved for recovery."); }
  }

  remove(id) {
    return this.exclusive(async () => {
      if (!this.entries.has(id)) return;
      await this.fs.unlink(path.join(this.directory, `${id}.chunk`));
      this.entries.delete(id);
      await this.syncDirectory();
    });
  }

  list(sessionId) {
    return [...this.entries.entries()].filter(([, entry]) => !sessionId || entry.meta.session_id === sessionId)
      .sort(([, left], [, right]) => left.meta.epoch - right.meta.epoch || left.meta.start_ms - right.meta.start_ms || left.meta.sequence - right.meta.sequence)
      .map(([id, entry]) => ({ id, ...entry }));
  }

  async discard(sessionId) {
    for (const entry of this.list(sessionId)) await this.remove(entry.id);
  }
}
