import test from "node:test";
import assert from "node:assert/strict";
import { transcriptView } from "../meeting/transcript-view.mjs";

test("echoes and uncertain chunks stay internal while evidence links resolve", () => {
  const result = transcriptView([
    { id: "remote", final: true },
    { id: "echo", final: true, possible_echo_of: "remote" },
    { id: "uncertain", final: false },
    { id: "new", final: true },
  ]);
  assert.deepEqual(result.visible.map((item) => item.id), ["remote", "new"]);
  assert.equal(result.aliases.get("echo"), "remote");
});

test("missing or uncertain echo targets cannot hide valid speech", () => {
  const result = transcriptView([{ id: "uncertain", final: false }, { id: "valid", final: true, possible_echo_of: "uncertain" }, { id: "missing", final: true, possible_echo_of: "absent" }]);
  assert.deepEqual(result.visible.map((item) => item.id), ["valid", "missing"]);
});
