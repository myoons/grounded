import { app, BrowserWindow, ipcMain, shell } from "electron";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";
import { MeetingClient } from "../meeting-client.mjs";
import { startMeetingServer } from "../meeting-server.mjs";
import { sourceLink } from "../source-link.mjs";

const origin = process.env.ROOMSHIFT_TEST_API_URL || "http://127.0.0.1:8881";
assert.notEqual(new URL(origin).port, "8877", "Use an isolated staging API, not production.");
const client = new MeetingClient(origin);
const directory = path.join(os.homedir(), "Library/Application Support/Roomshift/artifacts/history-live-ui");
let window, server, sourceOpened = false, attemptedCapture = 0;
const deadline = setTimeout(() => app.exit(1), 45000);
const evaluate = (code) => window.webContents.executeJavaScript(code, true);

async function until(code) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(code)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error(`History UI condition failed: ${code}`);
}

async function main() {
  try {
    const capabilities = await client.command({ type: "capabilities" });
    assert.equal(capabilities.existing_session, null);
    const history = await client.command({ type: "history" });
    assert.equal(history.meetings.length, 1, "Use the isolated single-fixture history database.");
    const identifier = history.meetings[0].id;
    const saved = await client.command({ type: "state", session_id: identifier });
    assert.ok(saved.archived);
    assert.equal(saved.state, "stopped");
    const checked = saved.candidates.find((candidate) => candidate.research?.state === "ready" && candidate.research.citations.length);
    assert.ok(checked);
    assert.match(checked.statement, /sqlite/i);
    await app.whenReady();
    await mkdir(directory, { recursive: true });
    server = await startMeetingServer(fileURLToPath(new URL("../meeting/", import.meta.url)));
    window = new BrowserWindow({ show: false, width: 1320, height: 900, webPreferences: {
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      preload: fileURLToPath(new URL("../meeting-preload.cjs", import.meta.url)),
    } });
    ipcMain.handle("meeting:command", async (_event, command) => {
      if (command.type === "discovery") return { status: "ready", meetings: [] };
      if (command.type === "spool_status") return { pending: 0, failed: 0, corrupt: 0, meetings: [] };
      if (["start", "action", "native_audio_start", "native_audio_stop", "spool_resume", "spool_finish", "spool_pause", "spool_delete", "spool_quarantine"].includes(command.type)) {
        attemptedCapture++;
        throw Error("Archived viewing must not start capture or inference.");
      }
      if (command.type === "open_source") {
        const source = await client.command({ ...command, type: "source" });
        assert.ok(sourceLink(source.url).startsWith("https://"));
        if (process.env.ROOMSHIFT_TEST_OPEN_SOURCE === "1") await shell.openExternal(sourceLink(source.url));
        sourceOpened = true;
        return { opened: true };
      }
      return client.command(command);
    });
    ipcMain.handle("meeting:audio", () => { attemptedCapture++; throw Error("No history audio uploads allowed."); });
    await window.loadURL(server.origin + "/");
    await until("document.querySelector('.history-card') !== null");
    await writeFile(path.join(directory, "list.png"), (await window.webContents.capturePage()).toPNG());
    await evaluate("navigator.mediaDevices.getUserMedia = async () => { throw Error('No microphone capture allowed'); }; document.querySelector('.history-card').click();");
    await until("document.getElementById('session-state').textContent === 'Saved meeting'");
    const selector = `.candidate[data-id="${checked.id}"]`;
    await evaluate(`if (!document.querySelector(${JSON.stringify(selector + " .card-details")}).open) document.querySelector(${JSON.stringify(selector + " .card-title")}).click()`);
    await until(`document.querySelector(${JSON.stringify(selector + " .card-details")}).open`);
    await evaluate(`document.querySelector(${JSON.stringify(selector + " .quote-link")}).click(); document.querySelector(${JSON.stringify(selector + " .source-link")}).click();`);
    await until("document.querySelector('.utterance .highlight') !== null");
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(sourceOpened, true);
    await writeFile(path.join(directory, "finding.png"), (await window.webContents.capturePage()).toPNG());
    await evaluate("document.getElementById('back').click()");
    await until("!document.getElementById('lobby').hidden");
    const persisted = await client.command({ type: "state", session_id: identifier });
    assert.ok(persisted.presentation.expanded.includes(checked.id));
    await window.loadURL(server.origin + "/");
    await until("document.querySelector('.history-card') !== null");
    await evaluate("document.querySelector('.history-card').click()");
    await until(`document.querySelector(${JSON.stringify(selector + " .card-details")})?.open`);
    assert.equal(attemptedCapture, 0);
    assert.equal((await client.command({ type: "capabilities" })).existing_session, null);
    const report = { passed: true, syntheticOriginalAudio: true, realBackendAndSQLite: true,
      serverRestartPerformedByHarness: process.env.ROOMSHIFT_TEST_RESTARTED === "1",
      reopenedAfterRendererReload: true, sourceResolved: sourceOpened,
      sourceBrowserNavigationVerified: false, transcriptNavigationVerified: true,
      sourceBrowserLaunchRequested: process.env.ROOMSHIFT_TEST_OPEN_SOURCE === "1",
      viewingStatePersisted: true, captureAttempts: attemptedCapture,
      localSpoolFixture: "empty; mutation and recovery commands rejected",
      candidateCount: saved.candidates.length, transcriptCount: saved.segments.length };
    await writeFile(path.join(directory, "result.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    window?.destroy();
    await server?.close();
    app.exit(process.exitCode || 0);
  }
}
void main();
