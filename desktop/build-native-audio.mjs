import { mkdir } from "node:fs/promises";
import { spawnSync } from "node:child_process";

await mkdir("release/native", { recursive: true });
for (const [command, args] of [
  ["xcrun", ["swiftc", "-swift-version", "5", "-target", "arm64-apple-macos14.2", "-O", "native/SystemAudioTap.swift", "native/PCMConverter.swift", "native/AudioCapture.swift", "-o", "release/native/grounded-audio"]],
  ["codesign", ["--force", "--sign", "-", "release/native/grounded-audio"]],
  ["release/native/grounded-audio", ["--self-test"]],
]) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status || 1);
}
