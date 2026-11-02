export class UtteranceChunker {
  constructor(onChunk, offsetMs = 0, threshold = 0.006) {
    this.onChunk = onChunk;
    this.offsetMs = offsetMs;
    this.threshold = threshold;
    this.samples = 0;
    this.sequence = 0;
    this.preRoll = [];
    this.blocks = [];
    this.speechEndMs = 0;
    this.emittedSpeechEndMs = offsetMs;
    this.quietMs = 0;
  }

  push(samples) {
    if (!(samples instanceof Float32Array) || samples.length !== 3200)
      throw Error("Expected a 200 ms audio block.");
    const startMs = this.offsetMs + this.samples / 16;
    this.samples += samples.length;
    const endMs = this.offsetMs + this.samples / 16;
    let sum = 0;
    for (const value of samples) {
      if (!Number.isFinite(value)) throw Error("Invalid audio sample.");
      sum += value * value;
    }
    const rms = Math.sqrt(sum / samples.length);
    const block = { samples, startMs, endMs };
    const audible = rms >= this.threshold;
    if (audible) {
      this.speechEndMs = endMs;
      this.quietMs = 0;
    } else this.quietMs += 200;
    if (!this.blocks.length) {
      this.preRoll.push(block);
      if (this.preRoll.length > 2) this.preRoll.shift();
      if (audible) {
        this.blocks = this.preRoll;
        this.preRoll = [];
      }
    } else this.blocks.push(block);
    if (this.blocks.length >= 30) this.emit(true);
    else if (this.blocks.length && this.quietMs >= 600) this.emit(false);
    return { rms, audible, endMs };
  }

  emit(continuation) {
    const blocks = this.blocks;
    if (!blocks.length) return;
    this.blocks = continuation ? blocks.slice(-6) : [];
    this.preRoll = continuation ? [] : blocks.slice(-2);
    if (this.speechEndMs <= blocks[0].startMs || this.speechEndMs <= this.emittedSpeechEndMs) return;
    const buffer = new ArrayBuffer(blocks.length * 3200 * 2);
    const view = new DataView(buffer);
    let position = 0;
    for (const block of blocks)
      for (const sample of block.samples) {
        view.setInt16(
          position,
          Math.round(Math.max(-1, Math.min(1, sample)) * 32767),
          true,
        );
        position += 2;
      }
    this.emittedSpeechEndMs = this.speechEndMs;
    this.onChunk({
      sequence: this.sequence++,
      start_ms: blocks[0].startMs,
      end_ms: blocks.at(-1).endMs,
      speech_end_ms: this.speechEndMs,
      pcm: buffer,
    });
  }

  discard() {
    this.blocks = [];
    this.preRoll = [];
  }

  flush() {
    this.emit(false);
    this.discard();
  }
}
