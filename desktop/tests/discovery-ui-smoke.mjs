import { app, BrowserWindow, ipcMain } from "electron";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";
import { startMeetingServer } from "../meeting-server.mjs";
import { NativeFixture } from "./native-fixture.mjs";

const directory = path.join(os.homedir(), "Library/Application Support/Roomshift/artifacts/discovery-ui");
const meeting = { id: "https://meet.google.com/abc-defg-hij", title: "Architecture review", url: "https://meet.google.com/abc-defg-hij" };
const segment = { id: "speech-1", source: "system", text: "We can use SQLite WAL over a network share.", start_ms: 0, end_ms: 2200, speech_end_ms: 2100, final: true };
const echo = { ...segment, id: "speech-echo", source: "microphone", possible_echo_of: segment.id };
const uncertain = { ...segment, id: "speech-noise", text: "UNCERTAIN_NOISE_FIXTURE", final: false };
let session, starts = 0, uploads = 0, window, server, native;
const savedMeetings = new Map();
let sourceOpens = 0;
const deadline = setTimeout(() => { console.error("Discovery UI timed out"); app.exit(1); }, 30000);
const evaluate = (code) => window.webContents.executeJavaScript(code, true);
async function until(code) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(code)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error(`UI condition failed: ${code}`);
}
async function main() {
try {
  await app.whenReady();
  console.log("Electron ready");
  await mkdir(directory, { recursive: true });
  server = await startMeetingServer(fileURLToPath(new URL("../meeting/", import.meta.url)));
  window = new BrowserWindow({ show: false, width: 1320, height: 900, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: fileURLToPath(new URL("../meeting-preload.cjs", import.meta.url)) } });
  window.webContents.on("console-message", (event) => console.log("Renderer:", event.message));
  native = new NativeFixture(window);
  ipcMain.handle("meeting:command", (_event, command) => {
    if (command.type === "spool_status") return { pending: 0, failed: 0, meetings: [], corrupt: 0 };
    if (command.type === "spool_resume") return {};
    if (command.type === "spool_finish") return { drained: true, pending: 0 };
    if (command.type === "native_audio_start") return native.start(command.stream_id);
    if (command.type === "native_audio_stop") { native.stop(command.stream_id); return {}; }
    if (command.type === "discovery") return { status: "ready", meetings: [meeting] };
    if (command.type === "capabilities") return { speech_ready: true, detector_ready: true, search_enabled: true };
    if (command.type === "history") return { meetings: [...savedMeetings.values()].map((saved) => ({ id: saved.id, title: saved.title, created_at: saved.created_at, candidate_count: saved.candidates.length })), next_offset: null };
    if (command.type === "presentation") {
      const target = session?.id === command.session_id ? session : savedMeetings.get(command.session_id);
      target.presentation = command.presentation;
      return { saved: true };
    }
    if (command.type === "state" && savedMeetings.has(command.session_id)) return { ...savedMeetings.get(command.session_id), archived: true };
    if (command.type === "open_source") { sourceOpens++; return { opened: true }; }
    if (command.type === "start") {
      assert.equal(command.detected_id, meeting.id);
      starts++;
      session = { id: "session-1", epoch: 1, state: "capturing", elapsed_ms: 0, cloud_detection: true, research_enabled: true, detector: {}, segments: [segment, echo, uncertain], candidates: [{ id: "claim-1", category: "technical_behavior", statement: "SQLite WAL works over a network share.", question: "Does SQLite WAL support network filesystems?", state: "candidate", evidence: [echo], conditions: [], missing_information: [], research_revision: 1, research_attempts: 1, research: { state: "ready", verdict: "contradicted", summary: "SQLite WAL requires processes on the same host; it does not support network filesystems.", recommendation: "Use a client-server database for writers on multiple machines.", citations: [{ source_id: "S1", quote: "WAL does not work over a network filesystem." }], sources: [{ id: "S1", url: "https://www.sqlite.org/wal.html", title: "Write-Ahead Logging" }], caveats: [] } }] };
      const template = session.candidates[0];
      session.title = "Architecture review";
      session.created_at = "2026-11-02T01:00:00Z";
      for (let index = 1; index < 16; index++) {
        const candidate = structuredClone(template);
        candidate.id = `claim-${index + 1}`;
        const variant = index % 6;
        if (variant === 1) {
          candidate.statement = "Python 3.8 has reached end of life.";
          candidate.category = "current_status";
          candidate.research.verdict = "supported";
          candidate.research.summary = "Yes. Python 3.8 no longer receives upstream security updates.";
        } else if (variant === 2) {
          candidate.statement = "Object versioning can recover overwritten files.";
          candidate.category = "capability";
          candidate.research.verdict = "conditional";
          candidate.research.summary = "Only if versioning was enabled before the overwrite.";
        } else if (variant === 3) {
          candidate.statement = "Redis is always twice as fast as Memcached.";
          candidate.category = "performance_comparison";
          candidate.research.verdict = "insufficient";
          candidate.research.summary = "Not established. No matched benchmark resolves this universal claim.";
        } else if (variant === 4) candidate.research = { state: "searching" };
        else if (variant === 5) candidate.research = { state: "error", message: "Sources could not be retrieved. Retry this check." };
        if (variant > 0 && variant < 4) {
          candidate.research.citations = [];
          candidate.research.sources = [];
          candidate.research.recommendation = "Synthetic layout fixture, not a live research result.";
        }
        session.candidates.push(candidate);
      }
    }
    if (command.type === "action") {
      if (command.action === "retry_detection") session.detector = { state: "waiting", blocked: false };
      else session.state = command.action === "stop" ? "stopped" : command.action === "pause" ? "paused" : "capturing";
    }
    if (command.type === "clear") { savedMeetings.set(session.id, structuredClone(session)); session = undefined; }
    return session;
  });
  ipcMain.handle("meeting:audio", () => { uploads++; return {}; });
  await window.loadURL(server.origin + "/");
  console.log("Lobby loaded");
  await until("document.querySelector('.meeting-card') !== null");
  assert.equal(starts, 0);
  assert.equal(uploads, 0);
  assert.equal(await evaluate("document.getElementById('room').hidden"), true);
  await writeFile(path.join(directory, "lobby.png"), (await window.webContents.capturePage()).toPNG());
  await evaluate(`window.fixtureContexts = []; window.fixtureStreams = [];
    window.confirm = () => true;
    const syntheticStream = async () => { const context = new AudioContext({sampleRate:16000}); const destination = context.createMediaStreamDestination(); window.fixtureContexts.push(context); window.fixtureStreams.push(destination.stream); return destination.stream; };
    navigator.mediaDevices.getUserMedia = syntheticStream;
    document.querySelector('.meeting-card').click();`);
  await until("document.querySelector('.finding')?.textContent.includes('same host')");
  assert.equal(starts, 1);
  assert.equal(await evaluate("document.getElementById('transcript-count').textContent"), "1");
  assert.equal(await evaluate("document.getElementById('transcript').textContent.includes('UNCERTAIN_NOISE_FIXTURE')"), false);
  await evaluate("document.querySelector('.card-details').open = true; document.querySelector('.quote-link').click()");
  assert.equal(await evaluate("document.querySelector('.utterance .highlight')?.dataset.id"), "speech-1");
  await evaluate("document.querySelector('.card-details').open = false");
  assert.equal(await evaluate("document.getElementById('lobby').hidden"), true);
  await writeFile(path.join(directory, "room.png"), (await window.webContents.capturePage()).toPNG());
  assert.equal(await evaluate("document.querySelectorAll('.candidate').length"), 16);
  assert.equal(await evaluate("getComputedStyle(document.getElementById('candidates')).gridTemplateColumns.split(' ').length"), 4);
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
  await evaluate("document.querySelector('.card-title').click()");
  await until("document.querySelector('.card-details').open");
  await writeFile(path.join(directory, "expanded.png"), (await window.webContents.capturePage()).toPNG());
  await evaluate("document.querySelector('.card-title').click()");
  window.setSize(900, 800);
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(await evaluate("getComputedStyle(document.getElementById('candidates')).gridTemplateColumns.split(' ').length"), 2);
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
  await writeFile(path.join(directory, "narrow.png"), (await window.webContents.capturePage()).toPNG());
  session.detector = { state: "error", blocked: true, error: "Synthetic detector timeout" };
  await until("!document.getElementById('retry-checks').hidden");
  await evaluate("document.getElementById('retry-checks').click()");
  await until("document.getElementById('retry-checks').hidden");
  window.webContents.send("meeting:stop", { reason: "Meeting ended.", action: "stop" });
  session.state = "stopped";
  await until("window.fixtureStreams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))");
  await until("!document.getElementById('back').hidden");
  await evaluate("document.querySelector('.card-details').open = true");
  await evaluate("document.getElementById('back').click()");
  await until("!document.getElementById('lobby').hidden");
  await until("document.querySelector('.history-card') !== null");
  const uploadsBeforeHistory = uploads;
  await evaluate("document.querySelector('.history-card').click()");
  await until("document.getElementById('session-state').textContent === 'Saved meeting'");
  await until("document.querySelector('.card-details').open");
  assert.equal(await evaluate("document.querySelectorAll('.candidate').length"), 16);
  await evaluate("document.querySelector('.quote-link').click(); document.querySelector('.source-link').click()");
  await until("document.querySelector('.utterance .highlight') !== null");
  assert.equal(sourceOpens, 1);
  assert.equal(uploads, uploadsBeforeHistory);
  assert.equal(starts, 1);
  await writeFile(path.join(directory, "history.png"), (await window.webContents.capturePage()).toPNG());
  await evaluate("document.getElementById('back').click()");
  await until("!document.getElementById('lobby').hidden");
  await writeFile(path.join(directory, "result.json"), JSON.stringify({ passed: true, synthetic: true, nativeMeetVerified: false, starts, audioUploadsBeforeSelection: 0 }));
  console.log("Synthetic lobby → selected room → claim/result → meeting-close passed.");
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  clearTimeout(deadline);
  native?.stop();
  server?.close();
  window?.destroy();
  app.exit(process.exitCode || 0);
}
}
void main();
