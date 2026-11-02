import { app, safeStorage } from "electron";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { AudioSpool } from "../audio-spool.mjs";

const phase = process.argv.at(-1);
assert.ok(["write", "read"].includes(phase), "Specify write or read phase");
const directory = path.join(os.homedir(), "Library/Application Support/Roomshift/artifacts/release-0.7-safe-storage");
app.setPath("userData", path.join(directory, "electron-profile"));
const deadline = setTimeout(() => {
  console.error("OS key storage check timed out; inspect any permission prompt manually.");
  app.exit(2);
}, 30000);

async function main() {
  try {
    await app.whenReady();
    assert.equal(await safeStorage.isAsyncEncryptionAvailable(), true);
    assert.notEqual(safeStorage.getSelectedStorageBackend?.(), "basic_text");
    const spool = new AudioSpool(path.join(directory, "spool"), safeStorage);
    const meta = { session_id: "12345678-1234-4234-8234-123456789abc", epoch: 1,
      source: "microphone", sequence: 0, start_ms: 0, end_ms: 200, speech_end_ms: 190, age_ms: 0 };
    const synthetic = new Uint8Array(6400).fill(45).buffer;
    if (phase === "write") {
      await fs.mkdir(directory, { recursive: true, mode: 0o700 });
      await fs.chmod(directory, 0o700);
      const existing = await fs.readdir(spool.directory).catch((error) => {
        if (error.code === "ENOENT") return [];
        throw error;
      });
      assert.equal(existing.length, 0, "Never overwrite prior encryption evidence automatically");
      await spool.append(meta, synthetic);
    } else {
      await spool.initialize();
    }
    const entries = spool.list();
    assert.equal(entries.length, 1);
    const identifier = spool.identity(meta);
    assert.deepEqual((await spool.read(identifier)).bytes, synthetic);
    const chunk = await fs.readFile(path.join(spool.directory, `${identifier}.chunk`));
    assert.equal(chunk.includes(Buffer.from(synthetic)), false);
    assert.equal(chunk.includes(Buffer.from(meta.session_id)), false);
    assert.equal((await fs.stat(spool.directory)).mode & 0o777, 0o700);
    for (const filename of ["key.enc", `${identifier}.chunk`]) {
      assert.equal((await fs.stat(path.join(spool.directory, filename))).mode & 0o777, 0o600);
    }
    const report = { phase, passed: true, platform: process.platform, electron: process.versions.electron,
      os_encryption_available: true, insecure_backend: false, synthetic_only: true,
      process_restart_read: phase === "read", permissions: "directory 700; key/chunk 600",
      packaged_app_keychain_acceptance: false, timestamp: new Date().toISOString() };
    await fs.writeFile(path.join(directory, `${phase}-report.json`), JSON.stringify(report, null, 2), { mode: 0o600 });
    console.log(JSON.stringify(report));
    app.exit(0);
  } catch (error) {
    console.error(error.message);
    await fs.mkdir(directory, { recursive: true, mode: 0o700 });
    await fs.writeFile(path.join(directory, `${phase}-report.json`), JSON.stringify({
      phase, passed: false, platform: process.platform, electron: process.versions.electron,
      error: error.message, synthetic_only: true, packaged_app_keychain_acceptance: false,
      timestamp: new Date().toISOString(),
    }, null, 2), { mode: 0o600 });
    app.exit(1);
  } finally {
    clearTimeout(deadline);
  }
}

void main();
