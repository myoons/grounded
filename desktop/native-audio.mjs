import { spawn } from "node:child_process";

export class PCMFrames {
  constructor(emit) { this.emit = emit; this.pending = Buffer.alloc(0); }
  push(bytes) {
    if (bytes.length > 262144) throw Error("Oversized native audio packet.");
    this.pending = Buffer.concat([this.pending, bytes]);
    while (this.pending.length >= 12800) {
      const frame = new Float32Array(3200);
      for (let index = 0; index < 3200; index++) {
        frame[index] = this.pending.readFloatLE(index * 4);
        if (!Number.isFinite(frame[index])) throw Error("Invalid native audio sample.");
      }
      this.pending = this.pending.subarray(12800);
      this.emit(frame);
    }
  }

  flush() {
    if (!this.pending.length) return;
    const padding = Buffer.alloc(12800 - this.pending.length);
    this.push(padding);
  }
}

export class NativeAudio {
  constructor(executable, emit, launch = spawn) {
    this.executable = executable;
    this.emit = emit;
    this.launch = launch;
  }

  async start(streamId) {
    if (this.active) throw Error("System audio is already active.");
    if (typeof streamId !== "string" || !/^[a-f0-9-]{36}$/.test(streamId)) throw Error("Invalid audio stream identifier.");
    const child = this.launch(this.executable, [], { stdio: ["ignore", "pipe", "pipe"], env: { HOME: process.env.HOME, PATH: process.env.PATH, TMPDIR: process.env.TMPDIR } });
    const run = { child, streamId, sequence: 0, outstanding: new Set(), stderr: "" };
    this.active = run;
    await new Promise((resolve, reject) => {
      run.reject = reject;
      const fail = (code) => {
        if (this.active !== run) return;
        if (run.finishing) {
          if (code !== "NATIVE_AUDIO_EXITED") run.failure = Error(code);
          return;
        }
        this.emit({ streamId, state: "error", code });
        reject(Error(code));
        void this.stop(streamId);
      };
      run.timer = setTimeout(() => fail("NATIVE_AUDIO_START_TIMEOUT"), 25000);
      const frames = new PCMFrames((samples) => {
        if (this.active !== run) return;
        if (run.outstanding.size >= 8) { fail("NATIVE_AUDIO_BACKPRESSURE"); return; }
        clearTimeout(run.timer);
        resolve();
        const sequence = run.sequence++;
        run.outstanding.add(sequence);
        this.emit({ streamId, sequence, samples });
      });
      run.frames = frames;
      child.stdout.on("data", (bytes) => {
        if (this.active !== run) return;
        try { frames.push(bytes); } catch { fail("INVALID_NATIVE_PCM"); }
      });
      child.stderr.on("data", (bytes) => {
        if (this.active !== run) return;
        run.stderr += bytes.toString();
        if (run.stderr.length > 16384) { fail("NATIVE_AUDIO_PROTOCOL_ERROR"); return; }
        const lines = run.stderr.split("\n");
        run.stderr = lines.pop();
        for (const line of lines) {
          let status;
          try { status = JSON.parse(line); } catch { continue; }
          if (status.state === "error") fail("NATIVE_AUDIO_FAILED");
        }
      });
      child.once("error", () => fail("NATIVE_AUDIO_UNAVAILABLE"));
      child.once("exit", () => fail("NATIVE_AUDIO_EXITED"));
    });
    return { streamId };
  }

  acknowledge(streamId, sequence) {
    if (this.active?.streamId === streamId) this.active.outstanding.delete(sequence);
  }

  async stop(streamId, flush = false) {
    const run = this.active;
    if (!run || (streamId && run.streamId !== streamId)) return;
    if (run.finishing) return run.finishing;
    if (flush) {
      run.finishing = Promise.resolve().then(() => this.finish(run));
      return run.finishing;
    }
    this.active = undefined;
    clearTimeout(run.timer);
    run.reject?.(Error("System audio stopped."));
    const child = run.child;
    if (child.exitCode !== null || child.signalCode) return;
    await new Promise((resolve) => {
      const timer = setTimeout(() => { child.kill("SIGKILL"); resolve(); }, 1500);
      child.once("exit", () => { clearTimeout(timer); resolve(); });
      child.kill("SIGTERM");
    });
  }

  async finish(run) {
    clearTimeout(run.timer);
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        run.failure = Error("Native final audio drain timed out.");
        run.child.kill("SIGKILL");
        resolve();
      }, 1500);
      run.child.once("close", () => { clearTimeout(timer); resolve(); });
      run.child.kill("SIGTERM");
    });
    try { run.frames?.flush(); }
    finally { if (this.active === run) this.active = undefined; }
    if (run.failure) throw run.failure;
  }
}
