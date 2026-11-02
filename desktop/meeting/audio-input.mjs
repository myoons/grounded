import { UtteranceChunker } from "./chunker.mjs";
import { captureError, captureFailure } from "./capture-status.mjs";

export class AudioInput {
  constructor(
    source,
    anchor,
    onChunk,
    onStatus,
    media = navigator.mediaDevices,
  ) {
    if (source !== "microphone") throw Error("Use native capture for system audio.");
    this.source = source;
    this.anchor = anchor;
    this.onChunk = onChunk;
    this.onStatus = onStatus;
    this.media = media;
    this.stopped = false;
    this.stream = undefined;
    this.context = undefined;
    this.timer = undefined;
    this.lastFrame = 0;
    this.verified = false;
  }

  async start() {
    this.startedAt = performance.now();
    this.onStatus({
      state: "permission",
      message: "Requesting permission",
      rms: 0,
    });
    try {
      const request = this.media.getUserMedia({
              video: false,
              audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
              },
            });
      let timeout;
      request.then((late) => { if (this.stopped) late.getTracks().forEach((track) => track.stop()); }).catch(() => {});
      let stream;
      try {
        stream = await Promise.race([request, new Promise((_resolve, reject) => { timeout = setTimeout(() => reject(captureError("CAPTURE_TIMEOUT", "Audio capture did not start within 25 seconds.")), 25000); })]);
      } finally { clearTimeout(timeout); }
      if (this.stopped) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      const audio = stream.getAudioTracks();
      this.diagnostic("track_received");
      if (!audio.length) throw captureError("NO_AUDIO_TRACK", "The capture route returned no audio track.");
      if (audio.some((track) => track.readyState !== "live")) throw captureError("AUDIO_TRACK_ENDED", "The audio track is already ended. Check macOS system-audio permission.");
      for (const track of stream.getTracks()) {
        track.onended = () =>
          this.fail("Audio device or sharing ended. Reconnect inputs.", "TRACK_ENDED");
      }
      this.onStatus({
        state: "starting",
        message: "Preparing audio processor",
        rms: 0,
        deviceLabel: audio[0].label || "System default input",
      });
      for (const track of audio) {
        track.onmute = () => {
          this.diagnostic("track_muted");
          this.onStatus({
            state: "muted",
            message: "Audio track muted; sound is not verified",
            rms: 0,
          });
        };
      }
      this.context = new AudioContext({ sampleRate: 16000 });
      if (this.context.sampleRate !== 16000)
        throw new Error("16 kHz capture is unavailable on this device.");
      await this.context.audioWorklet.addModule("./pcm-worklet.js");
      if (this.stopped) return;
      this.node = new AudioWorkletNode(this.context, "meeting-pcm");
      this.input = this.context.createMediaStreamSource(new MediaStream(audio));
      this.silent = this.context.createGain();
      this.silent.gain.value = 0;
      this.input.connect(this.node);
      this.node.connect(this.silent);
      this.silent.connect(this.context.destination);
      this.chunker = new UtteranceChunker(
        (chunk) => this.onChunk({ ...chunk, source: this.source }),
        performance.now() - this.anchor,
      );
      this.node.port.onmessage = (event) => {
        if (event.data === "flushed") { this.flushAck?.(); return; }
        if (this.stopped) return;
        this.lastFrame = performance.now();
        try {
          const level = this.chunker.push(event.data);
          if (!this.receivedSamples) { this.receivedSamples = true; this.diagnostic("samples_received"); }
          if (level.audible && !this.verified) this.diagnostic("audible_samples_received");
          this.verified ||= level.audible;
          this.onStatus({
            state: audio.some((track) => track.muted) ? "muted" : level.audible
              ? "signal"
              : this.verified
                ? "silent"
                : "unverified",
            message: level.audible
              ? "Audio signal received"
              : this.verified
                ? "Silence; samples still arriving"
                : "Samples arriving; audible input not verified yet",
            rms: level.rms,
          });
        } catch {
          this.fail("Audio processing failed. Reconnect inputs.", "PROCESSING_FAILED");
        }
      };
      this.node.onprocessorerror = () =>
        this.fail("Audio processor stopped. Reconnect inputs.", "PROCESSOR_STOPPED");
      await this.context.resume();
      if (this.stopped) return;
      this.lastFrame = performance.now();
      this.timer = setInterval(() => {
        if (performance.now() - this.lastFrame > 2500)
          this.fail(
            "No audio samples are arriving. Check permission and reconnect inputs.",
            "NO_AUDIO_SAMPLES",
          );
      }, 500);
    } catch (error) {
      if (!this.stopped) {
        const failure = captureFailure(error);
        this.fail(failure.message, failure.code, error);
      }
    }
  }

  diagnostic(code, error) {
    const metadata = { source: this.source, route: "microphone", code, elapsed_ms: Math.round(performance.now() - this.startedAt), tracks: this.stream?.getTracks().map((track) => ({ kind: track.kind, readyState: track.readyState, muted: track.muted, enabled: track.enabled })) || [], error_name: ["NotAllowedError", "AbortError", "NotFoundError", "NotReadableError", "Error"].includes(error?.name) ? error.name : undefined };
    console.info("Capture diagnostic", JSON.stringify(metadata));
    this.onStatus({ state: "diagnostic", diagnostic: metadata });
  }

  fail(message, code = "CAPTURE_FAILED", error) {
    if (this.stopped) return;
    this.diagnostic(code, error);
    void this.stop();
    this.onStatus({ state: "error", message, code, rms: 0 });
  }

  async stop({ flush = false } = {}) {
    if (this.stopping) return this.stopping;
    this.stopping = this.close(flush);
    return this.stopping;
  }

  async close(flush) {
    if (this.stopped) return;
    let flushError;
    clearInterval(this.timer);
    if (flush && this.node && this.context?.state === "running") {
      this.input?.disconnect();
      try {
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(Error("Microphone final audio flush timed out.")), 1500);
          this.flushAck = () => { clearTimeout(timeout); resolve(); };
          this.node.port.postMessage("flush");
        });
      } catch (error) { flushError = error; }
    }
    this.stopped = true;
    clearInterval(this.timer);
    if (flush) this.chunker?.flush();
    else this.chunker?.discard();
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.port.close();
      this.node.disconnect();
    }
    this.input?.disconnect();
    this.silent?.disconnect();
    this.stream?.getTracks().forEach((track) => {
      track.onended = null;
      track.onmute = null;
      track.stop();
    });
    if (this.context && this.context.state !== "closed")
      await this.context.close().catch(() => {});
    if (flushError) throw flushError;
  }
}
