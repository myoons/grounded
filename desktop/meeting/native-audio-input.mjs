import { UtteranceChunker } from "./chunker.mjs";

export class NativeAudioInput {
  constructor(anchor, onChunk, onStatus, bridge = window.meetingBridge) {
    Object.assign(this, { anchor, onChunk, onStatus, bridge });
    this.source = "system";
    this.streamId = crypto.randomUUID();
    this.stopped = false;
    this.sequence = 0;
  }

  async start() {
    this.onStatus({ state: "starting", message: "Starting native system audio" });
    this.unsubscribe = this.bridge.onSystemAudio((event) => {
      if (event.streamId !== this.streamId || this.stopped) return;
      if (event.state === "error") { this.fail(event.code); return; }
      try {
        if (event.sequence !== this.sequence++ || !(event.samples instanceof Float32Array) || event.samples.length !== 3200) throw Error("Invalid native frame sequence.");
        this.lastFrame = performance.now();
        this.chunker ||= new UtteranceChunker((chunk) => this.onChunk({ ...chunk, source: "system" }), Math.max(0, this.lastFrame - this.anchor - 200));
        const level = this.chunker.push(event.samples);
        this.verified ||= level.audible;
        this.onStatus({ state: level.audible ? "signal" : this.verified ? "silent" : "unverified", message: level.audible ? "System audio received" : "System audio samples arriving", rms: level.rms });
      } catch { this.fail("NATIVE_AUDIO_PROCESSING_FAILED"); }
    });
    try {
      await this.bridge.command({ type: "native_audio_start", stream_id: this.streamId });
      if (this.stopped) { await this.bridge.command({ type: "native_audio_stop", stream_id: this.streamId }); return; }
      this.timer = setInterval(() => { if (performance.now() - this.lastFrame > 2500) this.fail("NO_NATIVE_AUDIO_SAMPLES"); }, 500);
    } catch { if (!this.stopped) this.fail("NATIVE_AUDIO_START_FAILED"); }
  }

  fail(code) {
    if (this.stopped) return;
    console.info("Capture diagnostic", JSON.stringify({ source: "system", route: "native-coreaudio-tap", code }));
    void this.stop();
    this.onStatus({ state: "error", code, message: "Meeting audio unavailable. Check System Audio Recording permission and reconnect." });
  }

  async stop({ flush = false } = {}) {
    if (this.stopping) return this.stopping;
    this.stopping = this.close(flush);
    return this.stopping;
  }

  async close(flush) {
    if (this.stopped) return;
    clearInterval(this.timer);
    let flushError;
    if (flush) {
      try { await this.bridge.command({ type: "native_audio_stop", stream_id: this.streamId, flush: true }); }
      catch (error) { flushError = error; }
    }
    this.stopped = true;
    this.unsubscribe?.();
    clearInterval(this.timer);
    if (flush) this.chunker?.flush();
    else this.chunker?.discard();
    await this.bridge.command({ type: "native_audio_stop", stream_id: this.streamId }).catch(() => {});
    if (flushError) throw flushError;
  }
}
