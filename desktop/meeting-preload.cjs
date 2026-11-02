const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("meetingBridge", {
  command: (command) => ipcRenderer.invoke("meeting:command", command),
  audio: (metadata, bytes) =>
    ipcRenderer.invoke("meeting:audio", metadata, bytes),
  onSystemAudio: (callback) => {
    const listener = (_event, frame) => {
      try { callback(frame); }
      finally { if (Number.isSafeInteger(frame.sequence)) ipcRenderer.send("meeting:system-audio-ack", frame.streamId, frame.sequence); }
    };
    ipcRenderer.on("meeting:system-audio", listener);
    return () => ipcRenderer.removeListener("meeting:system-audio", listener);
  },
  onStop: (callback) => {
    const listener = (_event, reason) => callback(reason);
    ipcRenderer.on("meeting:stop", listener);
    return () => ipcRenderer.removeListener("meeting:stop", listener);
  },
});
