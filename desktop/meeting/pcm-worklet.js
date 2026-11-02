class MeetingPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(3200);
    this.position = 0;
    this.port.onmessage = (event) => {
      if (event.data !== "flush") return;
      if (this.position) this.port.postMessage(this.buffer, [this.buffer.buffer]);
      this.buffer = new Float32Array(3200);
      this.position = 0;
      this.port.postMessage("flushed");
    };
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let sample = 0; sample < channels[0].length; sample++) {
      let value = 0;
      for (const channel of channels) value += channel[sample];
      this.buffer[this.position++] = value / channels.length;
      if (this.position === 3200) {
        this.port.postMessage(this.buffer, [this.buffer.buffer]);
        this.buffer = new Float32Array(3200);
        this.position = 0;
      }
    }
    return true;
  }
}
registerProcessor("meeting-pcm", MeetingPCM);
