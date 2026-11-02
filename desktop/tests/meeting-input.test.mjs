import test from "node:test";
import assert from "node:assert/strict";
import { AudioInput } from "../meeting/audio-input.mjs";

function stream(state = "live") {
  const track = {
    readyState: state,
    stopped: false,
    stop() {
      this.stopped = true;
      this.readyState = "ended";
    },
  };
  return { track, getTracks: () => [track], getAudioTracks: () => [track] };
}

test("a permission result arriving after stop is closed without creating a processor", async () => {
  let resolve;
  const states = [];
  const input = new AudioInput(
    "microphone",
    0,
    () => {},
    (state) => states.push(state),
    {
      getUserMedia: () =>
        new Promise((accept) => {
          resolve = accept;
        }),
    },
  );
  const pending = input.start();
  await input.stop();
  const late = stream();
  resolve(late);
  await pending;
  assert.equal(late.track.stopped, true);
  assert.equal(input.context, undefined);
  assert.equal(
    states.some((state) => state.state === "signal"),
    false,
  );
});

test("denied permission and already-ended microphone tracks are explicit failures", async () => {
  const states = [];
  const denied = new AudioInput(
    "microphone",
    0,
    () => {},
    (state) => states.push(state),
    {
      getUserMedia: async () => {
        throw new DOMException("denied", "NotAllowedError");
      },
    },
  );
  await denied.start();
  assert.equal(states.at(-1).state, "error");
  assert.match(states.at(-1).message, /Permission denied/);
  const dead = stream("ended");
  const input = new AudioInput(
    "microphone",
    0,
    () => {},
    (state) => states.push(state),
    { getUserMedia: async () => dead },
  );
  await input.start();
  assert.equal(dead.track.stopped, true);
  assert.equal(states.at(-1).code, "AUDIO_TRACK_ENDED");
});
