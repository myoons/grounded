import assert from "node:assert/strict";
import test from "node:test";
import { MeetingDiscovery, startDiscovery } from "../meeting-discovery.mjs";

const room = { id: "native-window-1", title: "Design review", url: "https://meet.google.com/abc-defg-hij?authuser=1" };

test("only canonical Google Meet rooms appear; detection never starts recording", async () => {
  const callbacks = new Map();
  let config;
  const sdk = {
    addEventListener: (event, callback) => callbacks.set(event, callback),
    init: async (options) => { config = options; },
    startRecording: () => assert.fail("Detection must never record"),
  };
  const discovery = new MeetingDiscovery();
  await startDiscovery(sdk, discovery);
  assert.deepEqual(config.acquirePermissionsOnStartup, ["accessibility"]);
  callbacks.get("meeting-detected")({ window: room });
  assert.equal(discovery.snapshot().meetings.length, 1);
  assert.equal(discovery.snapshot().meetings[0].id, "https://meet.google.com/abc-defg-hij");
  discovery.update({ ...room, url: "https://meet.google.com/meet/abc-defg-hij?pli=1" });
  assert.equal(discovery.snapshot().meetings.length, 1);
  for (const url of ["https://meet.google.com.evil.test/abc-defg-hij", "http://meet.google.com/abc-defg-hij", "https://meet.google.com/", "https://user@meet.google.com/abc-defg-hij"]) discovery.update({ ...room, id: url, url });
  assert.equal(discovery.snapshot().meetings.length, 1);
});

test("duplicate detections update one room and close only after the last window", () => {
  const closed = [];
  const discovery = new MeetingDiscovery((id) => closed.push(id));
  discovery.update(room);
  discovery.update({ ...room, id: "second", title: "Updated title" });
  assert.equal(discovery.snapshot().meetings.length, 1);
  discovery.close(room.id);
  assert.equal(closed.length, 0);
  discovery.close("second");
  discovery.close("second");
  assert.equal(closed.length, 1);
  assert.equal(discovery.snapshot().meetings.length, 0);
  discovery.update(room);
  assert.equal(discovery.snapshot().meetings.length, 1);
});

test("SDK or permission failure invalidates stale meeting handles", () => {
  let closes = 0;
  const discovery = new MeetingDiscovery(() => closes++);
  discovery.update(room);
  discovery.invalidate();
  assert.equal(closes, 1);
  assert.equal(discovery.get("https://meet.google.com/abc-defg-hij"), null);
  assert.equal(discovery.snapshot().status, "unavailable");
});

test("navigation away from a known room invalidates that window", () => {
  const closed = [];
  const discovery = new MeetingDiscovery((id) => closed.push(id));
  discovery.update(room);
  discovery.update({ ...room, url: "https://meet.google.com/" });
  assert.equal(discovery.snapshot().meetings.length, 0);
  assert.deepEqual(closed, ["https://meet.google.com/abc-defg-hij"]);
});
