import { app, BrowserWindow, ipcMain } from "electron";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { MeetingClient } from "../meeting-client.mjs";
import { startMeetingServer } from "../meeting-server.mjs";
import { NativeFixture, wavSamples } from "./native-fixture.mjs";

const fixtures = process.env.ROOMSHIFT_TEST_FIXTURES || path.join(
  os.homedir(),
  "Library/Application Support/Roomshift/artifacts/meeting-fixtures",
);
const speechOnly = process.env.ROOMSHIFT_TEST_SPEECH_ONLY === "1";
const minimizedMs = Math.min(300000, Math.max(30000, Number(process.env.ROOMSHIFT_TEST_SOAK_MS) || 30000));
const artifacts = path.join(
  os.homedir(),
  `Library/Application Support/Roomshift/artifacts/${speechOnly ? "gpu-speech-ui-smoke" : "meeting-ui-smoke"}`,
);
const client = new MeetingClient(
  process.env.ROOMSHIFT_TEST_API_URL ?? "http://127.0.0.1:8881",
);
let window, server, identifier, native;
const delay = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));
const deadline = setTimeout(() => {
  console.error("Meeting UI smoke timed out");
  app.exit(1);
}, 240000 + minimizedMs);
const evaluate = (code) => window.webContents.executeJavaScript(code, true);
async function until(predicate, timeout = 90000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await predicate()) return;
    await delay(150);
  }
  throw Error(
    "Meeting acceptance timed out: " +
      (await evaluate("document.body.innerText")),
  );
}
async function click(id) {
  await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
}

async function main() {
  try {
    await app.whenReady();
    app.on("window-all-closed", () => {});
    await mkdir(artifacts, { recursive: true });
    const caps = await client.command({ type: "capabilities" });
    if (caps.existing_session)
      throw Error("A meeting already exists; use an isolated, empty staging service.");
    server = await startMeetingServer(
      fileURLToPath(new URL("../meeting/", import.meta.url)),
    );
    window = new BrowserWindow({
      show: false,
      width: 1280,
      height: 900,
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        preload: fileURLToPath(
          new URL("../meeting-preload.cjs", import.meta.url),
        ),
      },
    });
    native = new NativeFixture(window, wavSamples(await readFile(path.join(fixtures, "system.wav"))));
    ipcMain.handle("meeting:command", async (event, command) => {
      assert.equal(event.sender, window.webContents);
      if (command.type === "native_audio_start") return native.start(command.stream_id);
      if (command.type === "native_audio_stop") { native.stop(command.stream_id); return {}; }
      if (command.type === "discovery") return { status: "ready", meetings: [{ id: "synthetic-meeting", title: "Synthetic meeting", url: "https://meet.google.com/abc-defg-hij" }] };
      if (speechOnly && command.type === "start") {
        command = { ...command, cloud_detection: false, detection_consent_version: null, research_enabled: false, research_consent_version: null };
      }
      const result = await client.command(command);
      if (command.type === "start") identifier = result.id;
      return result;
    });
    ipcMain.handle("meeting:audio", (event, meta, bytes) => {
      assert.equal(event.sender, window.webContents);
      return client.audio(meta, bytes);
    });
    const sourceData = Object.fromEntries(
      await Promise.all(
        ["microphone", "system"].map(async (source) => [
          source,
          (await readFile(path.join(fixtures, `${source}.wav`))).toString(
            "base64",
          ),
        ]),
      ),
    );
    await window.loadURL(server.origin + "/");
    await evaluate(`(()=>{
      const fixtures=${JSON.stringify(sourceData)};
      const NativeContext=window.AudioContext;window.captureContexts=[];window.fixtureContexts=[];window.fixtureStreams=[];
      window.AudioContext=class extends NativeContext {constructor(options){super(options);window.captureContexts.push(this);}};
      async function makeStream(source){
        const context=new NativeContext({sampleRate:16000});window.fixtureContexts.push(context);
        const bytes=Uint8Array.from(atob(fixtures[source]),character=>character.charCodeAt(0));
        const original=await context.decodeAudioData(bytes.buffer);
        const buffer=context.createBuffer(1,original.length+16000*15,16000);
        buffer.copyToChannel(original.getChannelData(0),0,0);
        const player=context.createBufferSource();player.buffer=buffer;player.loop=true;const destination=context.createMediaStreamDestination();player.connect(destination);player.start();await context.resume();window.fixtureStreams.push(destination.stream);return destination.stream;
      }
      navigator.mediaDevices.getUserMedia=()=>makeStream('microphone');
    })()`);
    await until(() =>
      evaluate(
        "document.getElementById('session-state').textContent === 'Ready to listen'",
      ),
    );
    assert.equal(await evaluate("window.fixtureStreams.length"), 0);
    await evaluate("document.querySelector('.meeting-card').click()");
    await until(() =>
      evaluate(
        "[...document.querySelectorAll('.utterance small')].some(node=>node.textContent.startsWith('Microphone')) && [...document.querySelectorAll('.utterance small')].some(node=>node.textContent.startsWith('Meeting audio'))",
      ),
    );
    if (!speechOnly) {
      await until(() => evaluate("document.querySelectorAll('.candidate').length > 0"));
      await until(() => evaluate("!!document.querySelector('.candidate[data-research=ready]')"), 120000);
      const ready = await client.command({ type: "state", session_id: identifier });
      const checked = ready.candidates.find((candidate) => candidate.research?.state === "ready");
      assert.ok(checked.research.citations.length > 0, "A researched card needs verified source quotes");
      const source = await client.command({ type: "source", session_id: identifier, candidate_id: checked.id, source_id: checked.research.citations[0].source_id, revision: checked.research_revision });
      assert.ok(source.url.startsWith("https://"));
      await writeFile(path.join(artifacts, "research-checkpoint.json"), JSON.stringify({ syntheticAudio: true, nativeCaptureVerified: false, session: ready }, null, 2));
      await evaluate("document.querySelector('.candidate[data-research=ready] .card-details').open=true");
      await delay(300);
      await writeFile(path.join(artifacts, "research.png"), (await window.webContents.capturePage()).toPNG());
    }
    const before = await client.command({
      type: "state",
      session_id: identifier,
    });
    window.show();
    window.minimize();
    await delay(minimizedMs);
    const after = await client.command({
      type: "state",
      session_id: identifier,
    });
    assert.ok(
      after.segments.length > before.segments.length,
      "A second utterance after long silence must arrive while minimized",
    );
    assert.ok(
      after.segments.some((segment) => segment.source === "microphone"),
    );
    assert.ok(after.segments.some((segment) => segment.source === "system"));
    if (!speechOnly) {
      const candidate = after.candidates[0];
      await evaluate("window.originalCard=document.querySelector('.candidate')");
      await client.command({
        type: "review",
        session_id: identifier,
        candidate_id: candidate.id,
        action: "ignore",
      });
      await until(() =>
        evaluate("!!document.querySelector('.candidate.ignored')"),
      );
      assert.equal(
        await evaluate(
          "window.originalCard===document.querySelector('.candidate')",
        ),
        true,
      );
    }
    window.restore();
    await writeFile(
      path.join(artifacts, "window.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    await click("pause");
    await until(() =>
      evaluate(
        "window.captureContexts.every(context=>context.state==='closed')",
      ),
    );
    await until(() => evaluate("!document.getElementById('resume').disabled"));
    const paused = await client.command({
      type: "state",
      session_id: identifier,
    });
    assert.equal(paused.state, "paused");
    assert.equal(paused.queue_depth, 0);
    await click("resume");
    await until(() => evaluate("window.fixtureStreams.length===2"));
    const transport = client.transport;
    client.transport = async () => {
      throw new Error("Synthetic connection interruption");
    };
    await until(() =>
      evaluate(
        "window.captureContexts.every(context=>context.state==='closed')",
      ),
    );
    client.transport = transport;
    await until(() => evaluate("!document.getElementById('resume').disabled"));
    await click("resume");
    await until(() => evaluate("window.fixtureStreams.length===3"));
    await click("pause");
    await until(() =>
      evaluate(
        "window.captureContexts.every(context=>context.state==='closed')",
      ),
    );
    await until(() => evaluate("!document.getElementById('stop').disabled"));
    await click("stop");
    await until(() =>
      evaluate(
        "window.fixtureStreams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended')) && window.captureContexts.every(context=>context.state==='closed')",
      ),
    );
    await until(() => evaluate("document.getElementById('session-state').textContent==='Meeting ended'"));
    const stopped = await client.command({
      type: "state",
      session_id: identifier,
    });
    assert.equal(stopped.state, "stopped");
    assert.equal(stopped.queue_depth, 0);
    const result = {
      status: "passed",
      nativeCaptureVerified: false,
      meetPhoneVerified: false,
      syntheticAudio: true,
      realWhisper: true,
      realRunpod: true,
      realRunpodSpeech: true,
      detectorVerified: !speechOnly && !stopped.detector.blocked,
      researchVerified: !speechOnly,
      detectorHealthyAtStop: speechOnly ? null : !stopped.detector.blocked,
      minimizedSeconds: minimizedMs / 1000,
      beforeSegments: before.segments.length,
      afterSegments: after.segments.length,
      session: stopped,
    };
    await writeFile(
      path.join(artifacts, "result.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(
      JSON.stringify({
        status: result.status,
        beforeSegments: result.beforeSegments,
        afterSegments: result.afterSegments,
        candidates: stopped.candidates.length,
        detector: stopped.detector,
        nativeCaptureVerified: false,
      }),
    );
    await evaluate(
      "Promise.all(window.fixtureContexts.map(context=>context.close()))",
    );
    native?.stop();
    window.destroy();
    await server.close();
    clearTimeout(deadline);
    app.quit();
  } catch (error) {
    native?.stop();
    console.error(error);
    if (window && !window.isDestroyed()) {
      await writeFile(
        path.join(artifacts, "failure.png"),
        (await window.webContents.capturePage()).toPNG(),
      ).catch(() => {});
      window.destroy();
    }
    if (identifier)
      await client
        .command({ type: "action", session_id: identifier, action: "stop" })
        .catch(() => {});
    await server?.close();
    clearTimeout(deadline);
    app.exit(1);
  }
}
void main();
