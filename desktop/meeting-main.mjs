import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  powerMonitor,
  shell,
  safeStorage,
} from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MeetingClient } from "./meeting-client.mjs";
import { sourceLink } from "./source-link.mjs";
import {
  trustedMeetingFrame,
  allowsMeetingPermission,
} from "./meeting-permissions.mjs";
import { startMeetingServer } from "./meeting-server.mjs";
import RecallSdk from "@recallai/desktop-sdk";
import { MeetingDiscovery, startDiscovery } from "./meeting-discovery.mjs";
import { NativeAudio } from "./native-audio.mjs";
import { captureLogger } from "./capture-log.mjs";
import { AudioSpool } from "./audio-spool.mjs";
import { AudioSpoolController } from "./audio-spool-controller.mjs";

const directory = path.dirname(fileURLToPath(import.meta.url));
const client = new MeetingClient(
  process.env.ROOMSHIFT_API_URL ?? "http://127.0.0.1:8877",
);
let window,
  server,
  origin,
  armed = false,
  currentSession,
  quitting = false;
let selectedMeeting, selecting = false;
let nativeAudio;
let finalizing;
let spoolController;
const discovery = new MeetingDiscovery((identifier) => {
  if (selectedMeeting?.id === identifier) void finishCapture("Meeting ended.");
});

async function finishCapture(reason) {
  if (finalizing) return finalizing.promise;
  if (!currentSession && !selecting) return true;
  armed = false;
  let resolve;
  const pending = { promise: new Promise((done) => { resolve = done; }), resolve: (confirmed = true) => resolve(confirmed) };
  finalizing = pending;
  const timer = setTimeout(() => pending.resolve(false), 160000);
  if (window && !window.isDestroyed()) window.webContents.send("meeting:stop", { reason, action: "stop" });
  else pending.resolve(false);
  const confirmed = await pending.promise;
  clearTimeout(timer);
  if (currentSession) {
    armed = false;
    await nativeAudio?.stop();
  }
  if (finalizing === pending) finalizing = undefined;
  return confirmed;
}

function trusted(event) {
  if (
    !window ||
    event.sender !== window.webContents ||
    event.senderFrame !== window.webContents.mainFrame ||
    new URL(event.senderFrame.url).origin !== origin
  )
    throw Error("Untrusted meeting window.");
}

async function halt(reason, action = "pause") {
  armed = false;
  await nativeAudio?.stop();
  if (window && !window.isDestroyed())
    window.webContents.send("meeting:stop", { reason, action });
  client.abort();
}

async function quit() {
  if (quitting) return;
  quitting = true;
  if (!await finishCapture("Application closed; finishing captured audio.")) {
    quitting = false;
    await dialog.showMessageBox({
      type: "warning",
      message: "Grounded could not confirm that captured audio is safely saved.",
      detail: "The app will remain open to preserve pending audio. Resolve the pending audio warning and retry closing. Force quitting may lose audio that has not reached local storage.",
      buttons: ["Keep open"],
    });
    return;
  }
  spoolController?.close();
  if (window && !window.isDestroyed()) window.destroy();
  await RecallSdk.shutdown().catch(() => {});
  server?.close();
  app.quit();
}

async function main() {
  app.setName("Grounded");
  app.setPath(
    "userData",
    path.join(app.getPath("appData"), "Roomshift Research"),
  );
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on("second-instance", () => {
    window?.show();
    window?.focus();
  });
  await app.whenReady();
  spoolController = new AudioSpoolController(
    new AudioSpool(path.join(app.getPath("userData"), "audio-spool"), safeStorage),
    new MeetingClient(client.origin),
  );
  server = await startMeetingServer(path.join(directory, "meeting"));
  origin = server.origin;
  window = new BrowserWindow({
    title: "Grounded",
    width: 1240,
    height: 840,
    backgroundColor: "#fafafa",
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      partition: "meeting-research",
      preload: path.join(directory, "meeting-preload.cjs"),
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  const logCapture = captureLogger(app.getPath("userData"));
  window.webContents.on("console-message", (event) => logCapture(event.message));
  nativeAudio = new NativeAudio(app.isPackaged ? path.join(process.resourcesPath, "grounded-audio") : path.join(directory, "release/native/grounded-audio"), (event) => {
    if (event.state === "error") logCapture("Capture diagnostic " + JSON.stringify({ source: "system", route: "native-coreaudio-tap", code: event.code }));
    if (!window.isDestroyed()) window.webContents.send("meeting:system-audio", event);
  });
  window.webContents.on("will-navigate", (event, destination) => {
    if (destination !== origin + "/") event.preventDefault();
  });
  const session = window.webContents.session;
  session.setPermissionCheckHandler(
    (contents, permission, requested, details) =>
      armed &&
      trustedMeetingFrame(
        contents,
        window.webContents,
        origin,
        requested,
        details,
      ) &&
      allowsMeetingPermission(permission, details),
  );
  session.setPermissionRequestHandler(
    (contents, permission, callback, details) =>
      callback(
        armed &&
          trustedMeetingFrame(
            contents,
            window.webContents,
            origin,
            contents.getURL(),
            details,
          ) &&
          allowsMeetingPermission(permission, details),
      ),
  );
  ipcMain.handle("meeting:command", async (event, command) => {
    trusted(event);
    if (command?.type === "spool_quarantine") {
      if (armed) throw Error("Stop capture before resolving unreadable audio.");
      await spoolController.ready();
      const confirmation = await dialog.showMessageBox(window, {
        type: "warning", buttons: ["Cancel", "Quarantine unreadable audio"], defaultId: 0, cancelId: 0,
        message: "Keep unreadable audio isolated?",
        detail: "These encrypted files cannot be processed. They will be preserved on this Mac, never uploaded automatically, and still count toward the storage limit. Their meeting content may remain incomplete. Healthy pending audio is unchanged.",
      });
      if (confirmation.response !== 1) return { cancelled: true };
      if (armed) throw Error("Stop capture before resolving unreadable audio.");
      spoolController.authorized.clear();
      spoolController.client.abort();
      await spoolController.running;
      return spoolController.spool.quarantineCorrupt();
    }
    if (command?.type === "spool_status") {
      await spoolController.ready();
      return spoolController.status(command.session_id);
    }
    if (["spool_resume", "spool_finish", "spool_pause", "spool_delete"].includes(command?.type)) {
      if (!/^[0-9a-f-]{36}$/i.test(command.session_id ?? "")) throw Error("Invalid meeting identifier.");
      if (command.type === "spool_resume") return spoolController.authorize(command.session_id, true);
      if (command.type === "spool_finish") return spoolController.finish(command.session_id);
      if (command.type === "spool_pause") return spoolController.pause(command.session_id);
      if (command.session_id === currentSession) { armed = false; await nativeAudio.stop(); }
      return spoolController.discard(command.session_id);
    }
    if (command?.type === "native_audio_start") {
      if (!armed || !currentSession || finalizing || quitting) throw Error("Meeting capture is not active.");
      return nativeAudio.start(command.stream_id);
    }
    if (command?.type === "native_audio_stop") { await nativeAudio.stop(command.stream_id, command.flush === true); return { stopped: true }; }
    if (command?.type === "discovery") return { ...discovery.snapshot(), selected: selectedMeeting };
    if (command?.action === "resume" && !discovery.get(selectedMeeting?.id)) throw Error("This meeting is no longer available.");
    if (command?.type === "start") {
      await spoolController.ready();
      if (quitting || finalizing) throw Error("Meeting capture is stopping.");
      if (spoolController.status().pending || spoolController.status().corrupt) throw Error("Resolve retained audio before starting another capture.");
      const meeting = discovery.get(command.detected_id);
      if (!meeting) throw Error("This meeting is no longer available.");
      if (selecting) throw Error("A meeting is already opening.");
      selecting = true;
      try {
        if (currentSession) {
          if (selectedMeeting?.id !== meeting.id) throw Error("End the current meeting before opening another.");
          return await client.command({ type: "state", session_id: currentSession });
        }
        selectedMeeting = meeting;
        const result = await client.command({ ...command, title: meeting.title });
        currentSession = result.id;
        await spoolController.authorize(result.id);
        if (!discovery.get(meeting.id) || finalizing || quitting) {
          armed = false;
          window.webContents.send("meeting:stop", { reason: "Meeting ended during setup.", action: "stop" });
          return result;
        }
        armed = true;
        return result;
      } finally {
        selecting = false;
        if (!currentSession) finalizing?.resolve();
      }
    }
    if (command?.type === "open_source") {
      const source = await client.command({ ...command, type: "source" });
      await shell.openExternal(sourceLink(source.url));
      return { opened: true };
    }
    if (
      ["pause", "stop"].includes(command?.action) ||
      command?.type === "clear"
    )
      { armed = false; await nativeAudio.stop(); }
    if (command?.action === "resume" && spoolController.status().pending) throw Error("Finish retained audio before resuming capture.");
    if (command?.type === "clear" && spoolController.status(command.session_id).pending) throw Error("Finish or explicitly delete retained audio before leaving this meeting.");
    const result = await client.command(command);
    if (command.action === "resume") {
      currentSession = result.id;
      armed = true;
      await spoolController.authorize(result.id);
    }
    if (["pause", "stop"].includes(command.action)) spoolController.pause(command.session_id);
    if (command.type === "clear") { currentSession = undefined; selectedMeeting = undefined; }
    if (command.action === "stop") { currentSession = undefined; finalizing?.resolve(); }
    return command.type === "capabilities"
      ? {
          ...result,
          platform: process.platform,
          os_version: process.getSystemVersion(),
          system_audio_path:
            "Native Core Audio Tap, 16 kHz mono; system playback may include other apps",
        }
      : result;
  });
  ipcMain.handle("meeting:audio", (event, meta, bytes) => {
    trusted(event);
    if (meta?.session_id !== currentSession)
      throw Error("Meeting capture is not active.");
    return spoolController.append(meta, bytes);
  });
  ipcMain.on("meeting:system-audio-ack", (event, streamId, sequence) => {
    trusted(event);
    if (Number.isSafeInteger(sequence)) nativeAudio.acknowledge(streamId, sequence);
  });
  powerMonitor.on(
    "suspend",
    () => void halt("System sleep paused capture. Resume explicitly."),
  );
  powerMonitor.on("resume", () =>
    window?.webContents.send(
      "meeting:stop",
      { reason: "System resumed. Resume capture explicitly.", action: "pause" },
    ),
  );
  window.webContents.on("render-process-gone", () => {
    if (!quitting) void halt("Meeting window stopped.");
  });
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      void quit();
    }
  });
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      { label: "Grounded", submenu: [{ role: "quit" }] },
      { role: "editMenu" },
      { role: "windowMenu" },
    ]),
  );
  app.on("before-quit", (event) => {
    if (!quitting) {
      event.preventDefault();
      void quit();
    }
  });
  await window.loadURL(origin + "/");
  await startDiscovery(RecallSdk, discovery).catch(() => discovery.invalidate());
}
void main().catch(() => {
  dialog.showErrorBox(
    "Grounded could not start",
    "Check the local application and API configuration. No capture has started.",
  );
  app.exit(1);
});
