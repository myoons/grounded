import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { captureLogger } from "../capture-log.mjs";

test("capture diagnostics persist only allowlisted metadata with private permissions", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "grounded-log-"));
  try {
    const log = captureLogger(directory);
    log("Capture diagnostic null");
    log("Capture diagnostic " + JSON.stringify({ source: "system", route: "secret", code: "secret", error_name: "secret", error_message: "private spoken words", elapsed_ms: -3, tracks: [null, { kind: "secret", readyState: "secret" }] }));
    await log.flush();
    const target = path.join(directory, "capture-diagnostics.jsonl");
    const text = await readFile(target, "utf8");
    assert.ok(!text.includes("secret") && !text.includes("private spoken"));
    assert.ok(!("elapsed_ms" in JSON.parse(text)));
    assert.equal((await stat(target)).mode & 0o777, 0o600);
    assert.equal((await stat(directory)).mode & 0o777, 0o700);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
