import { app, BrowserWindow, ipcMain } from "electron";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { startMeetingServer } from "../meeting-server.mjs";

let window, server;
let state = "capturing", finalization = { state: "not_started" };
let stops = 0, clears = 0, captureAttempts = 0;
let backlog = 0, processingResumes = 0, failDrain = false, permanent = false, failPause = false, storageMode = "ready";
const identifier = "00000000-0000-4000-8000-000000000001";
const snapshot = () => ({ id: identifier, state, finalization, title: "Finalization fixture", epoch: 1, elapsed_ms: 0,
  detector: {}, candidates: [], segments: [], research_enabled: true, presentation: {} });
const evaluate = (source) => window.webContents.executeJavaScript(source, true);
async function until(source) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(source)) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw Error(`UI assertion timed out: ${source}`);
}
const deadline = setTimeout(() => app.exit(1), 20000);

async function main() {
  try {
    await app.whenReady();
    server = await startMeetingServer(fileURLToPath(new URL("../meeting/", import.meta.url)));
    window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false,
      preload: fileURLToPath(new URL("../meeting-preload.cjs", import.meta.url)) } });
    ipcMain.handle("meeting:command", async (_event, command) => {
      if (command.type === "spool_status") {
        if (storageMode === "hanging") return new Promise(() => {});
        if (storageMode === "failed") throw Error("Secure OS key storage did not respond. Capture is disabled.");
        return { pending: backlog, failed: permanent ? 1 : 0, meetings: backlog ? [{ session_id: identifier, pending: backlog, failed: permanent ? 1 : 0, retryable: !permanent, error: permanent ? "Transcription limit reached; retrying cannot raise this limit." : null }] : [] };
      }
      if (command.type === "spool_resume") { processingResumes++; return {}; }
      if (command.type === "spool_finish") {
        if (failDrain) throw Error("Transcription service unavailable; audio remains encrypted on this Mac.");
        backlog = 0;
        return { drained: true, pending: 0 };
      }
      if (command.type === "discovery") return { status: "ready", meetings: [] };
      if (command.type === "capabilities") return { speech_ready: true, existing_session: { id: identifier, state } };
      if (command.type === "state") return snapshot();
      if (command.type === "presentation") return snapshot();
      if (command.type === "history") return { meetings: [{ ...snapshot(), created_at: new Date().toISOString(), candidate_count: 0 }], next_offset: null };
      if (command.type === "action" && command.action === "pause") {
        if (failPause) throw Error("Pause acknowledgement lost");
        state = "paused";
        return snapshot();
      }
      if (command.type === "action" && command.action === "stop") {
        stops++;
        await new Promise((resolve) => setTimeout(resolve, 50));
        state = "finishing";
        finalization = { state: "pending" };
        return snapshot();
      }
      if (command.type === "clear") { clears++; return {}; }
      captureAttempts++;
      throw Error(`Unexpected operation: ${command.type}`);
    });
    ipcMain.handle("meeting:audio", () => { captureAttempts++; throw Error("No capture expected."); });
    await window.loadURL(server.origin + "/");
    await until("!document.getElementById('stop').disabled");
    await evaluate("document.getElementById('stop').click(); document.getElementById('stop').click()");
    await until("document.body.dataset.meeting === 'finishing'");
    assert.equal(stops, 1);
    await evaluate("document.getElementById('back').click()");
    await until("!document.getElementById('lobby').hidden && document.querySelector('.history-card')?.textContent.includes('Finishing checks')");
    assert.equal(clears, 0);
    await window.loadURL(server.origin + "/");
    await until("!document.getElementById('lobby').hidden && document.querySelector('.history-card') !== null");
    await evaluate("document.querySelector('.history-card').click()");
    await until("document.getElementById('session-state').textContent === 'Finishing checks…'");
    state = "stopped";
    finalization = { state: "incomplete", message: "Fixture failure" };
    await until("document.getElementById('session-state').textContent === 'Checks incomplete'");
    await evaluate("document.getElementById('back').click()");
    await until("document.querySelector('.history-card')?.textContent.includes('Checks incomplete')");
    await evaluate("document.querySelector('.history-card').click()");
    await until("document.getElementById('session-state').textContent === 'Checks incomplete'");
    assert.equal(captureAttempts, 0);
    const repeatedEndIdempotent = stops === 1;
    backlog = 2;
    failDrain = true;
    state = "paused";
    await window.loadURL(server.origin + "/");
    await until("!document.getElementById('pending-audio').hidden && document.querySelector('.pending-audio-row button') !== null");
    await evaluate("document.querySelector('.pending-audio-row button').click()");
    await until("document.getElementById('audio-recovery-error').textContent.includes('Transcription service unavailable')");
    assert.equal(backlog, 2);
    assert.equal(stops, 1);
    failDrain = false;
    await evaluate("document.querySelector('.pending-audio-row button').click()");
    await until("document.body.dataset.meeting === 'finishing'");
    assert.equal(backlog, 0);
    assert.equal(processingResumes, 2);
    assert.equal(stops, 2);
    assert.equal(captureAttempts, 0);
    state = "stopped";
    finalization = { state: "incomplete", message: "Interrupted after audio was saved." };
    await window.loadURL(server.origin + "/");
    await until("document.querySelector('.history-card') !== null");
    await evaluate("document.querySelector('.history-card').click()");
    await until("!document.getElementById('recover-checks').hidden");
    await evaluate("document.getElementById('recover-checks').click()");
    await until("document.body.dataset.meeting === 'finishing'");
    assert.equal(processingResumes, 3);
    assert.equal(stops, 3);
    assert.equal(captureAttempts, 0);
    state = "capturing";
    failPause = true;
    await window.loadURL(server.origin + "/");
    await until("!document.getElementById('pause').disabled");
    await evaluate("document.getElementById('pause').click()");
    await until("document.getElementById('audio-recovery-error').textContent.includes('Pause acknowledgement lost')");
    assert.equal(await evaluate("document.body.dataset.meeting"), "capturing");
    assert.equal(await evaluate("document.getElementById('session-state').textContent"), "Capture stopped · Pending processing");
    failPause = false;
    state = "capturing";
    backlog = 1;
    failDrain = true;
    await window.loadURL(server.origin + "/");
    await until("!document.getElementById('stop').disabled");
    await evaluate("document.getElementById('stop').click()");
    await until("document.getElementById('audio-recovery-error').textContent.includes('Transcription service unavailable')");
    assert.equal(await evaluate("document.getElementById('session-state').textContent"), "Capture stopped · Pending processing");
    assert.equal(await evaluate("document.getElementById('cards-empty').querySelector('h2').textContent"), "No claims yet");
    assert.equal(stops, 3);
    assert.equal(backlog, 1);
    permanent = true;
    await window.loadURL(server.origin + "/");
    await until("document.querySelector('.pending-audio-row button')?.disabled === true");
    assert.ok(await evaluate("document.querySelector('.pending-audio-row').textContent.includes('retrying cannot raise')"));
    state = "stopped";
    storageMode = "hanging";
    await window.loadURL(server.origin + "/");
    await until("document.querySelector('.history-card') !== null && !document.getElementById('lobby').hidden");
    storageMode = "failed";
    await window.loadURL(server.origin + "/");
    await until("document.querySelector('.history-card') !== null && !document.getElementById('audio-recovery-error').hidden");
    assert.ok(await evaluate("document.getElementById('audio-recovery-error').textContent.includes('Secure OS key storage did not respond')"));
    console.log(JSON.stringify({ passed: true, repeatedEndIdempotent, finishingNavigationPreserved: true,
      finishingReloadAndReopen: true, incompleteStatePersistedInList: true, recoveryRetainsOnFailure: true,
      explicitRecoveryFinalizesWithoutCapture: true, savedChecksRecoverWithoutAudio: true, failedEndNeverClaimsListening: true,
      failedPauseNeverFabricatesServerState: true, permanentCapacityDisablesResume: true,
      hangingStorageDoesNotBlockHistory: true, storageFailureVisibleInLobby: true, captureAttempts, syntheticBackend: true }));
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    window?.destroy();
    server?.close();
    app.exit(process.exitCode || 0);
  }
}
void main();
