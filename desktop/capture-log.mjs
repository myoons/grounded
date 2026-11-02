import { chmod, mkdir, open, rename, stat } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";

export function captureLogger(directory) {
  let pending = Promise.resolve();
  let queued = 0;
  let lastWarning = -Infinity;
  const warn = () => {
    if (performance.now() - lastWarning < 60000) return;
    lastWarning = performance.now();
    try { process.stderr.write("Grounded capture diagnostics incomplete.\n"); } catch {}
  };
  const codes = new Set(["track_received", "track_muted", "samples_received", "audible_samples_received", "AUDIO_TRACK_ENDED", "CAPTURE_FAILED", "CAPTURE_TIMEOUT", "NO_AUDIO_SAMPLES", "NO_AUDIO_TRACK", "PROCESSING_FAILED", "PROCESSOR_STOPPED", "TRACK_ENDED", "NATIVE_AUDIO_PROCESSING_FAILED", "NATIVE_AUDIO_START_FAILED", "NO_NATIVE_AUDIO_SAMPLES", "INVALID_NATIVE_PCM", "NATIVE_AUDIO_BACKPRESSURE", "NATIVE_AUDIO_EXITED", "NATIVE_AUDIO_FAILED", "NATIVE_AUDIO_PROTOCOL_ERROR", "NATIVE_AUDIO_START_TIMEOUT", "NATIVE_AUDIO_UNAVAILABLE", "PERMISSION_DENIED", "CAPTURE_CANCELLED"]);
  const errors = new Set(["NotAllowedError", "AbortError", "NotFoundError", "NotReadableError", "Error"]);
  const log = (message) => {
    if (typeof message !== "string" || !message.startsWith("Capture diagnostic ") || message.length > 5000) return;
    let data;
    try { data = JSON.parse(message.slice("Capture diagnostic ".length)); } catch { return; }
    if (!data || !["system", "microphone"].includes(data.source)) return;
    if (queued >= 32) { warn(); return; }
    const entry = { at: new Date().toISOString(), source: data.source,
      route: ["native-coreaudio-tap", "microphone"].includes(data.route) ? data.route : "unknown",
      code: codes.has(data.code) ? data.code : "unknown",
      tracks: Array.isArray(data.tracks) ? data.tracks.slice(0, 4).map((track) => ({ kind: track?.kind === "audio" ? "audio" : "unknown", readyState: ["live", "ended"].includes(track?.readyState) ? track.readyState : "unknown", muted: track?.muted === true, enabled: track?.enabled === true })) : [],
      error_name: errors.has(data.error_name) ? data.error_name : "unknown" };
    if (Number.isFinite(data.elapsed_ms) && data.elapsed_ms >= 0) entry.elapsed_ms = data.elapsed_ms;
    queued += 1;
    pending = pending.then(async () => {
      await mkdir(directory, { recursive: true, mode: 0o700 });
      await chmod(directory, 0o700);
      const target = path.join(directory, "capture-diagnostics.jsonl");
      const size = await stat(target).then((value) => value.size).catch((error) => {
        if (error.code === "ENOENT") return 0;
        throw error;
      });
      if (size + Buffer.byteLength(JSON.stringify(entry)) + 1 > 64000) {
        await rename(target, target + ".previous");
        await chmod(target + ".previous", 0o600);
      }
      const file = await open(target, constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
      try { await file.chmod(0o600); await file.writeFile(JSON.stringify(entry) + "\n"); } finally { await file.close(); }
    }).catch(warn).finally(() => { queued -= 1; });
  };
  log.flush = () => pending;
  return log;
}
