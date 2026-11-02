export function capturedChunk(chunk, elapsedMs) {
  const ageMs = Math.max(0, elapsedMs - chunk.speech_end_ms);
  if (!Number.isFinite(ageMs) || ageMs > 15000) throw Error("Captured audio is too old to accept safely.");
  return { ...chunk, age_ms: ageMs };
}

export class UploadQueue {
  constructor(send, onFailure) {
    this.send = send;
    this.onFailure = onFailure;
    this.pending = [];
    this.active = false;
    this.stopped = false;
    this.failure = undefined;
    this.waiters = new Set();
  }
  offer(chunk) {
    if (this.stopped) return false;
    if (this.pending.length >= 32) {
      this.stopped = true;
      this.onFailure("Local audio storage cannot keep up. Capture stopped; accepted writes are still being saved. The newest audio could not be accepted.");
      return false;
    }
    this.pending.push(chunk);
    void this.drain();
    return true;
  }
  async drain() {
    if (this.active || this.failure) return;
    this.active = true;
    try {
      while (this.pending.length && !this.failure) {
        const chunk = this.pending[0];
        await this.send(chunk);
        this.pending.shift();
      }
    } catch (error) {
      this.fail(error.message || "Local audio could not be saved. Capture stopped; do not close the app before reviewing pending audio.");
    } finally {
      this.active = false;
      this.settle();
    }
  }
  fail(message) {
    this.failure = Error(message);
    this.stop();
    this.onFailure(message);
  }
  stop() {
    this.stopped = true;
    this.settle();
  }

  retry() {
    if (this.active) return;
    this.failure = undefined;
    void this.drain();
  }

  settle() {
    if (!this.failure && (this.active || this.pending.length)) return;
    for (const waiter of this.waiters) {
      clearTimeout(waiter.timer);
      if (this.failure) waiter.reject(this.failure);
      else waiter.resolve();
    }
    this.waiters.clear();
  }

  finish(timeoutMs = 20000) {
    if (this.failure) return Promise.reject(this.failure);
    if (!this.active && !this.pending.length) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject };
      waiter.timer = setTimeout(() => {
        this.waiters.delete(waiter);
        reject(Error("Some captured audio has not been saved locally yet. Keep the app open and retry."));
      }, timeoutMs);
      this.waiters.add(waiter);
    });
  }
}
