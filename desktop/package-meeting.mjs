import { cp, mkdir, writeFile, rm, readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { frontendAssets } from "./meeting-server.mjs";

const directory = "release/meeting-stage";
const metadata = JSON.parse(await readFile("package.json", "utf8"));
const native = spawnSync(process.execPath, ["build-native-audio.mjs"], { stdio: "inherit" });
if (native.status !== 0) process.exit(native.status || 1);
await rm(directory, { recursive: true, force: true });
await mkdir(directory, { recursive: true });
for (const name of [
  "meeting-main.mjs",
  "meeting-discovery.mjs",
  "native-audio.mjs",
  "capture-log.mjs",
  "meeting-preload.cjs",
  "meeting-client.mjs",
  "audio-spool.mjs",
  "audio-spool-controller.mjs",
  "meeting-permissions.mjs",
  "meeting-server.mjs",
  "local-origin.mjs",
  "source-link.mjs",
  ...frontendAssets.map((name) => `meeting/${name}`),
]) {
  await mkdir(path.dirname(`${directory}/${name}`), { recursive: true });
  await cp(name, `${directory}/${name}`);
}
await writeFile(
  `${directory}/package.json`,
  JSON.stringify({
    name: "roomshift-research",
    version: metadata.version,
    private: true,
    type: "module",
    main: "meeting-main.mjs",
    dependencies: metadata.dependencies,
  }),
);
for (const name of ["@recallai/desktop-sdk", "uuid", "tar", "@isaacs/fs-minipass", "chownr", "minipass", "minizlib", "yallist"]) {
  await cp(`node_modules/${name}`, `${directory}/node_modules/${name}`, { recursive: true });
}
const result = spawnSync(
  "node_modules/.bin/electron-packager",
  [
    directory,
    "Roomshift Research",
    "--platform=darwin",
    "--arch=arm64",
    "--electron-version=45.1.0",
    "--out=release",
    "--overwrite",
    "--extra-resource=release/native/grounded-audio",
    "--asar.unpackDir=node_modules/@recallai/desktop-sdk",
    "--app-bundle-id=dev.roomshift.research",
    "--extend-info=MeetingInfo.plist",
  ],
  { stdio: "inherit" },
);
if (result.status !== 0) process.exit(result.status ?? 1);
const bundle = "release/Roomshift Research-darwin-arm64/Roomshift Research.app";
for (const args of [
  ["--force", "--deep", "--sign", "-", "--identifier", "dev.roomshift.research", bundle],
  ["--verify", "--deep", "--strict", bundle],
]) {
  const signing = spawnSync("codesign", args, { stdio: "inherit" });
  if (signing.status !== 0) process.exit(signing.status ?? 1);
}
