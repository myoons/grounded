export function captureFailure(error) {
  if (error.captureCode) return { code: error.captureCode, message: error.message };
  if (error.name === "NotAllowedError") return { code: "PERMISSION_DENIED", message: "Permission denied. Allow system audio or microphone access in macOS settings." };
  if (error.name === "AbortError") return { code: "CAPTURE_CANCELLED", message: "Audio capture was cancelled." };
  return { code: "CAPTURE_FAILED", message: "Audio capture could not start. See the internal capture log." };
}

export function captureError(code, message) {
  return Object.assign(new Error(message), { captureCode: code });
}

export function inputSummary(sources) {
  const failed = (source) => sources.get(source)?.state === "error";
  if (failed("system") && failed("microphone")) return "Audio unavailable";
  if (failed("system")) return "Microphone only";
  if (failed("microphone")) return "Meeting audio only";
  if (sources.get("system")?.state === "muted") return "Meeting audio muted";
  if ([...sources.values()].some((source) => ["permission", "starting"].includes(source.state))) return "Connecting audio…";
  return "Listening";
}
