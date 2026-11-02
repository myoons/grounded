const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export class AudioSpoolController {
  constructor(spool, client, options = {}) {
    this.spool = spool;
    this.client = client;
    this.sleep = options.sleep ?? sleep;
    this.random = options.random ?? Math.random;
    this.maxAttempts = options.maxAttempts ?? 6;
    this.pollMs = options.pollMs ?? 1000;
    this.pollLimit = options.pollLimit ?? 180;
    this.authorized = new Set();
    this.failures = new Map();
    this.permanentFailures = new Set();
    this.running = undefined;
    this.closed = false;
  }

  async ready() {
    await this.spool.exclusive(() => this.spool.initialize());
  }

  async authorize(sessionId, recover = false) {
    await this.ready();
    if (this.permanentFailures.has(sessionId)) throw Error(this.failures.get(sessionId));
    if (recover) await this.client.resumeProcessing(sessionId);
    this.failures.delete(sessionId);
    this.authorized.add(sessionId);
    this.kick();
    return { authorized: true };
  }

  async append(meta, bytes) {
    const result = await this.spool.append(meta, bytes);
    this.kick();
    return result;
  }

  pause(sessionId) {
    this.authorized.delete(sessionId);
    return { paused: true };
  }

  async discard(sessionId) {
    this.pause(sessionId);
    this.client.abort();
    await this.running;
    await this.client.cancelProcessing(sessionId);
    await this.spool.discard(sessionId);
    this.failures.delete(sessionId);
    this.permanentFailures.delete(sessionId);
    return { deleted: true };
  }

  status(sessionId) {
    const entries = this.spool.list(sessionId);
    const identifiers = new Set(entries.map((entry) => entry.meta.session_id));
    return {
      pending: entries.length,
      failed: [...identifiers].filter((id) => this.failures.has(id)).length + this.spool.corrupt.size,
      bytes: entries.reduce((total, entry) => total + entry.storedBytes, 0),
      corrupt: this.spool.corrupt.size,
      quarantined: this.spool.quarantined,
      retained_bytes: this.spool.retainedBytes,
      meetings: [...identifiers].map((id) => ({
        session_id: id,
        pending: entries.filter((entry) => entry.meta.session_id === id).length,
        failed: this.failures.has(id) ? 1 : 0,
        error: this.failures.get(id) ?? null,
        retryable: !this.permanentFailures.has(id),
        authorized: this.authorized.has(id),
      })),
    };
  }

  kick() {
    if (this.running || this.closed) return;
    this.running = this.drain().finally(() => {
      this.running = undefined;
      if (this.spool.list().some((entry) => this.authorized.has(entry.meta.session_id) && !this.failures.has(entry.meta.session_id))) this.kick();
    });
  }

  async drain() {
    while (!this.closed) {
      const entry = this.spool.list().find((item) => this.authorized.has(item.meta.session_id) && !this.failures.has(item.meta.session_id));
      if (!entry) return;
      const sessionId = entry.meta.session_id;
      try {
        await this.process(entry);
      } catch (error) {
        if (this.authorized.has(sessionId)) {
          const capacity = /AUDIO_CAPACITY_REACHED/.test(error.message);
          if (error.permanent || capacity) this.permanentFailures.add(sessionId);
          this.failures.set(sessionId, capacity
            ? "This meeting reached its transcription limit. Encrypted audio is retained; retrying cannot raise this limit. Keep the audio for support or explicitly delete it."
            : error.permanent
              ? "The service rejected this audio permanently. Encrypted audio is retained; resolve the rejection before recovery."
              : "Audio processing paused. Encrypted audio is retained; resume processing explicitly.");
          this.authorized.delete(sessionId);
        }
      }
    }
  }

  async process(entry) {
    const sessionId = entry.meta.session_id;
    for (let attempt = 0; attempt < this.maxAttempts && this.authorized.has(sessionId) && !this.closed; attempt++) {
      try {
        const stored = await this.spool.read(entry.id);
        let state = await this.client.audioStatus(entry.meta, entry.digest);
        if (state.status === "processed" || state.status === "no_speech") {
          await this.spool.remove(entry.id);
          return;
        }
        if (state.status === "failed" && state.retryable !== true) throw Object.assign(Error(state.code || "Permanent audio rejection"), { permanent: true });
        if (state.status !== "pending") await this.client.audio({ ...stored.meta, durable: true }, stored.bytes);
        for (let poll = 0; poll < this.pollLimit && this.authorized.has(sessionId) && !this.closed; poll++) {
          await this.sleep(this.pollMs);
          if (!this.authorized.has(sessionId) || this.closed) return;
          state = await this.client.audioStatus(entry.meta, entry.digest);
          if (state.status === "processed" || state.status === "no_speech") {
            await this.spool.remove(entry.id);
            return;
          }
          if (state.status === "failed") {
            if (state.retryable !== true) throw Object.assign(Error(state.code || "Permanent audio rejection"), { permanent: true });
            break;
          }
          if (state.status !== "pending") break;
        }
      } catch (error) {
        if (error.permanent || /AUDIO_CAPACITY_REACHED|CHUNK_CONFLICT|STALE_EPOCH|INVALID_|NOT_FOUND|NOT_AUTHORIZED/.test(error.message)) throw Object.assign(error, { permanent: true });
      }
      if (this.authorized.has(sessionId) && attempt + 1 < this.maxAttempts) await this.sleep(Math.min(30000, 1000 * 2 ** attempt) * (0.8 + this.random() * 0.4));
    }
    if (this.authorized.has(sessionId)) throw Error("Audio retry budget exhausted");
  }

  async finish(sessionId, timeoutMs = 120000) {
    const started = Date.now();
    this.kick();
    while (this.spool.list(sessionId).length) {
      if (!this.authorized.has(sessionId) || this.failures.has(sessionId)) throw Error(this.failures.get(sessionId) || "Audio remains encrypted on this Mac. Resume processing to finish this meeting.");
      if (Date.now() - started > timeoutMs) throw Error("Audio is still processing and remains encrypted on this Mac. Resume processing later.");
      await this.sleep(100);
      this.kick();
    }
    return { drained: true, pending: 0 };
  }

  close() {
    this.closed = true;
    this.authorized.clear();
    this.client.abort();
  }
}
