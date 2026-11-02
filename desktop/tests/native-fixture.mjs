import assert from "node:assert/strict";

export function wavSamples(bytes) {
  assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
  let data, valid = false;
  for (let offset = 12; offset + 8 <= bytes.length;) {
    const size = bytes.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (start + size > bytes.length) throw Error("Truncated WAV fixture.");
    const kind = bytes.toString("ascii", offset, offset + 4);
    if (kind === "fmt ") valid = bytes.readUInt16LE(start) === 1 && bytes.readUInt16LE(start + 2) === 1 && bytes.readUInt32LE(start + 4) === 16000 && bytes.readUInt16LE(start + 14) === 16;
    if (kind === "data") data = bytes.subarray(start, start + size);
    offset = start + size + (size % 2);
  }
  assert.ok(valid && data);
  const samples = new Float32Array(data.length / 2 + 240000);
  for (let index = 0; index < data.length / 2; index++) samples[index] = data.readInt16LE(index * 2) / 32768;
  return samples;
}

export class NativeFixture {
  constructor(window, samples = new Float32Array(32000)) { this.window = window; this.samples = samples; }
  start(streamId) {
    this.stop(); this.streamId = streamId;
    let sequence = 0, offset = 0;
    this.timer = setInterval(() => {
      const samples = new Float32Array(3200);
      for (let index = 0; index < 3200; index++) { samples[index] = this.samples[offset]; offset = (offset + 1) % this.samples.length; }
      this.window.webContents.send("meeting:system-audio", { streamId, sequence: sequence++, samples });
    }, 200);
    return { streamId };
  }
  stop(streamId) { if (streamId && streamId !== this.streamId) return; clearInterval(this.timer); this.timer = undefined; }
}
