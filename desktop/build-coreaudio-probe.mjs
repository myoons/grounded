import { cp, mkdir } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const bundle = "release/Grounded Audio Check.app/Contents";
await mkdir(`${bundle}/MacOS`, { recursive: true });
await cp("native/ProbeInfo.plist", `${bundle}/Info.plist`);
const result = spawnSync("xcrun", ["swiftc", "-swift-version", "5", "-target", "arm64-apple-macos14.2", "-O", "native/SystemAudioTap.swift", "native/CoreAudioProbe.swift", "-o", `${bundle}/MacOS/GroundedAudioCheck`], { stdio: "inherit" });
if (result.status !== 0) process.exit(result.status || 1);
const signing = spawnSync("codesign", ["--force", "--sign", "-", "release/Grounded Audio Check.app"], { stdio: "inherit" });
if (signing.status !== 0) process.exit(signing.status || 1);
console.log("Built native system-audio diagnostic app; no capture was started.");
