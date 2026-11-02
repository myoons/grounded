import { app, BrowserWindow, ipcMain } from "electron";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { startMeetingServer } from "../meeting-server.mjs";

let window, server, session, releaseStart;
let starts = 0, stops = 0, nativeStarts = 0, nativeStops = 0, uploads = 0;
let delayStart = true;
const meeting = { id: "https://meet.google.com/abc-defg-hij", title: "Startup race", url: "https://meet.google.com/abc-defg-hij" };
const evaluate = (source) => window.webContents.executeJavaScript(source, true);
async function until(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  throw Error("Startup/End race assertion timed out.");
}
const deadline = setTimeout(() => app.exit(1), 20000);
async function main() {
  try {
    await app.whenReady();
    server = await startMeetingServer(fileURLToPath(new URL("../meeting/", import.meta.url)));
    window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false,
      preload: fileURLToPath(new URL("../meeting-preload.cjs", import.meta.url)) } });
    ipcMain.handle("meeting:command", async (_event, command) => {
      if (command.type === "discovery") return { status: "ready", meetings: [meeting] };
      if (command.type === "capabilities") return { speech_ready: true };
      if (command.type === "history") return { meetings: [], next_offset: null };
      if (command.type === "spool_status") return { pending: 0, failed: 0, corrupt: 0, meetings: [] };
      if (command.type === "spool_resume") return {};
      if (command.type === "spool_finish") return { drained: true, pending: 0 };
      if (command.type === "native_audio_start") { nativeStarts++; return {}; }
      if (command.type === "native_audio_stop") { nativeStops++; return {}; }
      if (command.type === "start") {
        starts++;
        if (delayStart) await new Promise((resolve) => { releaseStart = resolve; });
        session = { id: "startup-session", state: "capturing", epoch: 1, elapsed_ms: 0, title: "Startup race",
          detector: {}, segments: [], candidates: [], research_enabled: true };
        return session;
      }
      if (command.type === "state") return session;
      if (command.type === "action" && command.action === "stop") {
        stops++;
        session = { ...session, state: "finishing" };
        return session;
      }
      throw Error(`Unexpected command ${command.type}/${command.action}`);
    });
    ipcMain.handle("meeting:audio", () => { uploads++; throw Error("Unexpected audio after End."); });
    for (const pendingPermission of [false, true]) {
      delayStart = !pendingPermission;
      session = undefined;
      await window.loadURL(server.origin + "/");
      await until(() => evaluate("Boolean(document.querySelector('.meeting-card'))"));
      await evaluate(`window.confirm = () => true;
        window.microphoneRequests = 0; window.closedTracks = 0;
        navigator.mediaDevices.getUserMedia = () => { window.microphoneRequests++;
          return new Promise(resolve => { window.releaseMicrophone = () => resolve({
            getTracks: () => [{ stop: () => window.closedTracks++ }], getAudioTracks: () => []
          }); }); };
        document.querySelector('.meeting-card').click();`);
      if (pendingPermission) await until(() => evaluate("window.microphoneRequests === 1"));
      else await until(() => Boolean(releaseStart));
      window.webContents.send("meeting:stop", { reason: "Meet ended during setup", action: "stop" });
      await until(() => evaluate("document.getElementById('session-state').textContent === 'Finishing checks…'"));
      if (pendingPermission) {
        await until(() => nativeStops > 0);
        await evaluate("window.releaseMicrophone()");
      } else releaseStart();
      await until(() => stops === (pendingPermission ? 2 : 1));
      await until(() => evaluate("document.body.dataset.meeting === 'finishing'"));
      if (pendingPermission) assert.ok(await evaluate("window.closedTracks > 0"));
      else {
        assert.equal(nativeStarts, 0);
        assert.equal(await evaluate("window.microphoneRequests"), 0);
      }
    }
    assert.equal(starts, 2);
    assert.equal(uploads, 0);
    console.log(JSON.stringify({ passed: true, delayedStartFinalizedWithoutCapture: true,
      delayedPermissionTrackClosed: true, stopAcknowledgements: stops, uploads, syntheticBackend: true }));
  } finally {
    clearTimeout(deadline);
    window?.destroy();
    server?.close();
  }
}
void main().then(() => app.exit(0), (error) => { console.error(error); app.exit(1); });
