import { AudioInput } from "./audio-input.mjs";
import { NativeAudioInput } from "./native-audio-input.mjs";
import { capturedChunk, UploadQueue } from "./upload-queue.mjs";
import { CandidateCards } from "./cards.mjs";
import { inputSummary } from "./capture-status.mjs";
import { transcriptView } from "./transcript-view.mjs";
import { CaptureLifecycle, captureStatus } from "./capture-lifecycle.mjs";

const bridge = window.meetingBridge;
const element = (id) => document.getElementById(id);
let session,
  capabilities,
  inputs = [],
  uploads,
  generation = 0,
  anchor = 0,
  connected = false,
  lastError = "",
  ownsSession = false,
  polling = false;
const captureLifecycle = new CaptureLifecycle();
let pauseOperation;
let startOperation;
let openingHistory = false;
let detectedMeeting, discoveryState;
let historyMode = false, historyNext = null, historyRefreshed = 0;
let restorePending = false, restoringPresentation = false, presentationTimer;
let presentationSaving = Promise.resolve(), saveError = "";
let pendingFinalizationError;
let spoolBusy = false;
let spoolSignature = "";
let consentAccepted = false;
let storageError = "", checkingSpool = false;
const transcripts = new Map();
const inputStates = new Map();
const cards = new CandidateCards(element("candidates"), (command) => bridge.command(command), () => poll(), error, (id) => {
  const target = transcripts.get(id);
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("highlight");
    setTimeout(() => target.classList.remove("highlight"), 1600);
  }
});
const labels = { microphone: "Microphone", system: "Meeting audio" };

function resetResults() {
  clearTimeout(presentationTimer);
  transcripts.clear();
  cards.clear();
  element("transcript").replaceChildren();
  element("candidates").replaceChildren();
  element("transcript-count").textContent = "0";
  element("candidate-count").textContent = "0";
}

function error(message = "") {
  lastError = message;
  if (message) console.warn("Meeting interrupted", message);
  buttons();
}
function sourceState(source, state) {
  if (state.state === "diagnostic") return;
  inputStates.set(source, state);
  if (state.state === "error") {
    error(`${labels[source]}: ${state.message}`);
  }
  buttons();
}
function buttons() {
  const state = session?.state;
  const busy = captureLifecycle.busy || openingHistory;
  element("retry-checks").hidden = state !== "capturing" || session?.detector?.state !== "error";
  element("retry-checks").disabled = busy || !connected;
  element("recover-checks").hidden = state !== "stopped" || session?.finalization?.state !== "incomplete";
  element("recover-checks").disabled = busy || spoolBusy || !connected;
  element("pause").disabled = busy || state !== "capturing";
  element("resume").disabled =
    busy || !connected || state !== "paused";
  element("stop").disabled = busy || !["capturing", "paused"].includes(state);
  element("pause").hidden = state !== "capturing";
  element("resume").hidden = state !== "paused";
  element("stop").hidden = !["capturing", "paused"].includes(state);
  const status = captureStatus({ phase: captureLifecycle.phase, state, hasInputs: inputs.length > 0,
    incomplete: session?.finalization?.state === "incomplete", archived: historyMode, interrupted: session?.interrupted });
  const preserveStatus = historyMode || (state === "capturing" && !inputs.length && captureLifecycle.phase !== "starting");
  element("session-state").textContent = preserveStatus ? status : state === "capturing" && connected && inputs.length && !captureLifecycle.busy
    ? session.detector?.blocked ? "Checks paused" : inputSummary(inputStates)
    : lastError ? "Needs attention" : connected ? status : "Connecting…";
  element("session-state").title = lastError || session?.finalization?.message || "";
  element("audio-recovery-error").textContent = [storageError, lastError].filter(Boolean).join(" ");
  element("audio-recovery-error").hidden = !storageError && !lastError;
  element("room").hidden = !session;
  element("lobby").hidden = Boolean(session);
  element("back").hidden = !session || ["capturing", "paused"].includes(state);
  element("room-title").textContent = session?.title || detectedMeeting?.title || "Meeting";
  const storageMessage = saveError || session?.storage_error || "";
  element("save-status").hidden = !storageMessage;
  element("save-status").textContent = storageMessage;
  document.body.dataset.meeting = state || "idle";
  element("transcript-empty").hidden = transcripts.size > 0;
  element("cards-empty").hidden = Boolean(session?.candidates.some((item) => item.state !== "ignored"));
  element("cards-empty").querySelector("h2").textContent = session && !session.research_enabled
    ? "Research is off for this meeting."
    : inputs.length ? "Listening for claims…" : session?.state === "finishing" || captureLifecycle.phase === "ending" ? "Finishing checks…" : "No claims yet";
  element("cards-empty").querySelector("p").textContent = session && !session.research_enabled
    ? "End this meeting and start listening again to enable checks."
    : "";
}
async function stopInputs(message) {
  const queue = uploads;
  const previous = inputs;
  inputs = [];
  const results = await Promise.allSettled(previous.map((input) => input.stop({ flush: true })));
  generation++;
  queue?.stop();
  const failure = results.find((result) => result.status === "rejected");
  if (failure) error(failure.reason?.message || "The last audio frame could not be saved.");
  try {
    await queue?.finish();
    if (uploads === queue) uploads = undefined;
  } catch (failure) { error(failure.message); }
  for (const source of Object.keys(labels))
    sourceState(source, { state: "paused", message, rms: 0 });
}
async function pause(reason = "Paused") {
  if (pauseOperation) return pauseOperation;
  const transition = captureLifecycle.begin("pausing");
  if (!transition) return;
  const operation = (async () => {
    await stopInputs(reason);
    if (uploads?.pending.length) throw Error("Some audio has not reached local storage. Keep the app open and retry saving before resuming capture.");
    if (session) await bridge.command({ type: "spool_finish", session_id: session.id });
    if (session && session.state === "capturing") {
      session = await bridge.command({ type: "action", session_id: session.id, action: "pause" });
    }
  })();
  pauseOperation = operation;
  buttons();
  try {
    await operation;
  } catch (failure) {
    error(failure.message);
  } finally {
    if (pauseOperation === operation) pauseOperation = undefined;
    captureLifecycle.finish(transition);
    buttons();
  }
}
async function capture() {
  inputStates.clear();
  const expected = ++generation;
  const identifier = session.id,
    epoch = session.epoch;
  uploads = new UploadQueue(
    (chunk) =>
      bridge.audio(
        {
          session_id: identifier,
          epoch,
          source: chunk.source,
          sequence: chunk.sequence,
          start_ms: chunk.start_ms,
          end_ms: chunk.end_ms,
          speech_end_ms: chunk.speech_end_ms,
          age_ms: chunk.age_ms,
        },
        chunk.pcm,
      ),
    (message) => {
      if (generation !== expected) return;
      error(message);
      if (captureLifecycle.phase !== "ending") void pause("Connection/backpressure interruption");
    },
  );
  inputs = ["system", "microphone"].map(
    (source) => {
      const onChunk = (chunk) => { if (generation === expected) uploads?.offer(capturedChunk(chunk, performance.now() - anchor)); };
      const onStatus = (state) => { if (generation === expected) sourceState(source, state); };
      return source === "system" ? new NativeAudioInput(anchor, onChunk, onStatus) : new AudioInput(source, anchor, onChunk, onStatus);
    },
  );
  await Promise.all(inputs.map((input) => input.start()));
  if (generation === expected && inputs.every((input) => input.stopped)) {
    error(
      "Neither input is available. Check permissions, then Resume / reconnect.",
    );
    await pause("Input setup failed");
  }
}
async function start(resume = false) {
  if (openingHistory || !connected || !capabilities?.speech_ready || !detectedMeeting) return;
  const transition = captureLifecycle.begin("starting");
  if (!transition) return;
  const operation = startCapture(resume, transition);
  startOperation = operation;
  try { await operation; }
  finally { if (startOperation === operation) startOperation = undefined; }
}
async function startCapture(resume, transition) {
  buttons();
  error();
  try {
    await stopInputs("Preparing capture");
    if (uploads?.pending.length) throw Error("Audio is still waiting for local storage. Keep the app open; do not start another capture yet.");
    const backlog = await bridge.command({ type: "spool_status" });
    if (!captureLifecycle.owns(transition)) return;
    if (backlog.corrupt) throw Error("Encrypted audio could not be read and is preserved. Resolve local recovery before starting capture.");
    if (backlog.pending || backlog.failed) throw Error("Resume pending audio processing before starting another capture.");
    if (!consentAccepted) {
      consentAccepted = window.confirm("Start meeting capture? Confirm that participants have been informed. Audio is encrypted and temporarily stored on this Mac until its transcription is durably saved, then deleted. Unprocessed audio remains until processing succeeds or you explicitly delete it. Storage limits stop capture rather than deleting pending audio. Audio is sent to Runpod; claims and context are sent to Runpod Kimi and Firecrawl for research.");
      if (!consentAccepted) return;
    }
    session = await bridge.command(
      resume
        ? { type: "action", session_id: session.id, action: "resume" }
        : {
            type: "start",
            detected_id: detectedMeeting?.id,
            consent: true,
            audio_consent_version: "runpod-audio-v2",
            cloud_detection: true,
            detection_consent_version: "runpod-kimi-k3-v1",
            research_enabled: true,
            research_consent_version: "firecrawl-kimi-v1",
            language: "en",
          },
    );
    if (!captureLifecycle.owns(transition)) return;
    if (!resume) {
      pendingFinalizationError = undefined;
      historyMode = false;
      resetResults();
    }
    anchor = performance.now() - session.elapsed_ms;
    ownsSession = true;
    await bridge.command({ type: "spool_resume", session_id: session.id });
    if (!captureLifecycle.owns(transition)) return;
    buttons();
    await capture();
  } catch (failure) {
    error(failure.message);
    await stopInputs("Capture not started");
  } finally {
    captureLifecycle.finish(transition);
    buttons();
  }
}
function timeLabel(milliseconds) {
  return `${Math.floor(milliseconds / 60000)}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, "0")}`;
}
function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function render() {
  if (!session) {
    buttons();
    return;
  }

  const detector = session.detector;
  if (detector.error && detector.blocked) {
    error(detector.error);
  }
  const pane = element("transcript"),
    follow = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
  const transcript = transcriptView(session.segments);
  for (const segment of transcript.visible) {
    if (transcripts.has(segment.id)) continue;
    let card = pane.lastElementChild;
    const canJoin = card?.dataset.source === segment.source && segment.final &&
      card.dataset.final === "true" && !segment.possible_echo_of &&
      segment.start_ms - Number(card.dataset.end) < 2000;
    if (!canJoin) {
      card = node("article", undefined, "utterance");
      card.dataset.source = segment.source;
      card.dataset.final = String(segment.final);
      card.append(node("small", `${labels[segment.source]} · ${timeLabel(segment.start_ms)}`), node("p"));
      pane.append(card);
    }
    const text = node("span", (canJoin ? " " : "") + segment.text);
    text.dataset.id = segment.id;
    card.querySelector("p").append(text);
    card.dataset.end = String(segment.end_ms);
    transcripts.set(segment.id, text);
    if (anchor)
      console.debug("Transcript display timing", {
        segmentId: segment.id,
        latencyMs: Math.max(
          0,
          Math.round(performance.now() - anchor - segment.speech_end_ms),
        ),
      });
  }
  for (const [duplicate, original] of transcript.aliases) {
    if (transcripts.has(original)) transcripts.set(duplicate, transcripts.get(original));
  }
  if (follow) pane.scrollTop = pane.scrollHeight;
  element("transcript-count").textContent = String(transcript.visible.length);
  cards.render(session);
  if (restorePending) {
    restorePending = false;
    restoringPresentation = true;
    const saved = session.presentation || {};
    for (const card of cards.items.values()) card.querySelector(".card-details").open = (saved.expanded || []).includes(card.dataset.id);
    requestAnimationFrame(() => {
      element("transcript").scrollTop = saved.transcript_scroll || 0;
      element("candidates").scrollTop = saved.cards_scroll || 0;
      requestAnimationFrame(() => { restoringPresentation = false; });
    });
  }
  element("candidate-count").textContent = String(session.candidates.filter((item) => item.state !== "ignored").length);
  buttons();
}
async function poll() {
  if (polling) return;
  polling = true;
  const pollGeneration = generation;
  try {
    if (!checkingSpool) {
      checkingSpool = true;
      void refreshSpool().then(() => { storageError = ""; }, (failure) => {
        storageError = `Local audio storage unavailable: ${failure.message}`;
      }).finally(() => { checkingSpool = false; buttons(); });
    }
    discoveryState = await bridge.command({ type: "discovery" });
    if (!detectedMeeting && discoveryState.selected) detectedMeeting = discoveryState.selected;
    renderMeetings();
    if (!capabilities || !connected || !capabilities.speech_ready) {
      capabilities = await bridge.command({ type: "capabilities" });
      if (!session && capabilities.existing_session && ["capturing", "paused"].includes(capabilities.existing_session.state)) {
        session = await bridge.command({
          type: "state",
          session_id: capabilities.existing_session.id,
        });
        if (session.state === "capturing")
          error(
            "An existing session is capturing on another window. Pause it explicitly before taking over; this window has not opened any inputs.",
          );
      }
    }
    connected = true;
    if (!capabilities.speech_ready) error("Transcription is unavailable. Please check the service configuration.");
    if (!session && Date.now() - historyRefreshed > 10000) await loadHistory();
    if (session && !historyMode) {
      const requestedId = session.id;
      const next = await bridge.command({
        type: "state",
        session_id: requestedId,
      });
      if (generation !== pollGeneration || session?.id !== requestedId) return;
      session = next;
      if ((session.state !== "capturing" || session.error) && inputs.length) {
        await stopInputs("Capture interrupted");
        if (session.state === "capturing") await pause();
      }
      if (session.error) error(session.error);
      render();
    }
  } catch (failure) {
    if (generation !== pollGeneration) return;
    connected = false;
    error(failure.message);
    if (inputs.length) await pause("Connection lost");
    if (failure.message.includes("[SESSION_NOT_FOUND]")) {
      session = undefined;
      ownsSession = false;
      capabilities = undefined;
      resetResults();
      error(
        "The server no longer has this in-memory session. Start a new session explicitly; previous capture will not resume.",
      );
    }
  } finally {
    polling = false;
    buttons();
  }
}
element("resume").onclick = () => void start(true);
element("retry-checks").onclick = async () => {
  if (!session || session.state !== "capturing") return;
  try {
    session = await bridge.command({ type: "action", session_id: session.id, action: "retry_detection" });
    error();
    render();
  } catch (failure) { error(failure.message); }
};
element("pause").onclick = () => void pause();
element("recover-checks").onclick = async () => {
  if (!session || session.state !== "stopped" || spoolBusy || captureLifecycle.busy || openingHistory) return;
  spoolBusy = true;
  buttons();
  const identifier = session.id;
  try {
    await bridge.command({ type: "spool_resume", session_id: identifier });
    await bridge.command({ type: "spool_finish", session_id: identifier });
    const next = await bridge.command({ type: "action", session_id: identifier, action: "stop" });
    if (session?.id === identifier) {
      session = next;
      historyMode = false;
      error();
      render();
    }
  } catch (failure) { error(failure.message); }
  finally { spoolBusy = false; buttons(); }
};
async function endMeeting() {
  if (captureLifecycle.phase === "ending") return;
  if (!startOperation && !pauseOperation && (!session || !["capturing", "paused"].includes(session.state))) return;
  const transition = captureLifecycle.begin("ending");
  if (!transition) return;
  buttons();
  try {
    if (startOperation || pauseOperation) {
      await stopInputs("Meeting ended during capture setup");
      await Promise.allSettled([startOperation, pauseOperation].filter(Boolean));
    }
    if (!session || !["capturing", "paused"].includes(session.state)) return;
    const queue = uploads;
    const previous = inputs;
    inputs = [];
    const results = await Promise.allSettled(previous.map((input) => input.stop({ flush: true })));
    pendingFinalizationError ||= results.find((result) => result.status === "rejected")?.reason?.message;
    try { await queue?.finish(); }
    catch (failure) { throw Error(failure.message); }
    generation++;
    queue?.stop();
    uploads = undefined;
    const drained = await bridge.command({ type: "spool_finish", session_id: session.id });
    if (drained?.pending > 0 || drained?.failed > 0 || drained?.drained === false)
      throw Error("Audio is safely queued locally. Resume processing to finish this meeting; no microphone will be opened.");
    session = await bridge.command({
      type: "action",
      session_id: session.id,
      action: "stop",
      ...(pendingFinalizationError ? { finalization_error: pendingFinalizationError.slice(0, 400) } : {}),
    });
    error();
    render();
  } catch (failure) {
    error(failure.message);
  } finally {
    captureLifecycle.finish(transition);
    buttons();
  }
}
element("stop").onclick = () => void endMeeting();
element("quarantine-audio").onclick = async () => {
  try {
    await bridge.command({ type: "spool_quarantine" });
    await refreshSpool();
  } catch (failure) { error(failure.message); }
};
async function refreshSpool() {
  const status = await bridge.command({ type: "spool_status" });
  const meetings = (status.meetings || []).filter((meeting) => meeting.pending || meeting.failed);
  if (session && uploads?.pending.length) {
    const local = meetings.find((meeting) => meeting.session_id === session.id);
    if (local) local.pending += uploads.pending.length;
    else meetings.push({ session_id: session.id, title: session.title, pending: uploads.pending.length, failed: 1 });
  }
  const panel = element("pending-audio");
  panel.hidden = !status.corrupt && (!meetings.length || (inputs.length > 0 && !lastError && !status.failed));
  element("corrupt-audio").hidden = !status.corrupt;
  element("quarantine-audio").hidden = !status.corrupt;
  const signature = JSON.stringify(meetings);
  if (signature === spoolSignature || spoolBusy) return;
  spoolSignature = signature;
  const list = element("pending-audio-list");
  list.replaceChildren();
  for (const meeting of meetings) {
    const row = node("div", undefined, "pending-audio-row");
    row.append(node("span", `${meeting.title || "Saved meeting"} · ${meeting.pending} audio chunks pending${meeting.failed ? " · Needs attention" : ""}`));
    const resume = node("button", "Resume processing");
    resume.disabled = meeting.retryable === false;
    if (meeting.error) row.append(node("span", meeting.error));
    const remove = node("button", "Delete audio");
    const act = async (operation) => {
      if (spoolBusy) return;
      spoolBusy = true;
      resume.disabled = remove.disabled = true;
      try { await operation(); error(); }
      catch (failure) { error(failure.message); }
      finally { spoolBusy = false; spoolSignature = ""; await refreshSpool().catch(() => {}); }
    };
    resume.onclick = () => void act(async () => {
      if (session?.id === meeting.session_id && uploads?.pending.length) {
        uploads.retry();
        await uploads.finish();
      }
      await bridge.command({ type: "spool_resume", session_id: meeting.session_id });
      const result = await bridge.command({ type: "spool_finish", session_id: meeting.session_id });
      if (result?.pending > 0 || result?.failed > 0 || result?.drained === false) throw Error("Some audio is still pending. It remains stored on this Mac; retry processing when the service is available.");
      if (session?.id === meeting.session_id && !inputs.length && !captureLifecycle.busy) await endMeeting();
      else if (session?.id !== meeting.session_id) await bridge.command({ type: "action", session_id: meeting.session_id, action: "stop" });
    });
    remove.onclick = () => {
      if (session?.id === meeting.session_id && uploads?.pending.length) {
        error("Some audio has not been saved locally. Retry saving before deleting disk audio; keep the app open.");
        return;
      }
      if (!window.confirm("Permanently delete this meeting's pending audio? Any speech not yet transcribed cannot be recovered. Saved transcripts and findings will remain.")) return;
      void act(async () => {
        if (session?.id === meeting.session_id) {
          await stopInputs("Audio processing cancelled");
          if (uploads?.pending.length) throw Error("Some audio has not reached local storage. Keep the app open and retry saving first.");
        }
        await bridge.command({ type: "spool_delete", session_id: meeting.session_id });
      });
    };
    row.append(resume, remove);
    list.append(row);
  }
}
bridge.onStop(({ reason, action = "pause" }) => {
  if (action === "stop") { void endMeeting(); return; }
  error(reason);
  void pause(reason);
});
function renderMeetings() {
  const root = element("meeting-list");
  const signature = JSON.stringify(discoveryState.meetings);
  if (root.dataset.signature !== signature) {
    root.dataset.signature = signature;
    root.replaceChildren();
    for (const meeting of discoveryState.meetings) {
      const button = node("button", undefined, "meeting-card");
      button.append(node("span", "Google Meet · Detected", "eyebrow"), node("strong", meeting.title), node("span", meeting.url.replace("https://", ""), "meeting-url"), node("span", "Open meeting →", "meeting-open"));
      button.onclick = () => {
        if (captureLifecycle.busy || openingHistory) return;
        detectedMeeting = meeting;
        void start();
      };
      root.append(button);
    }
  }
  element("discovery-message").textContent = discoveryState.status === "permission_required"
    ? "Allow Accessibility for Grounded in System Settings to detect your meeting."
    : discoveryState.status === "unavailable" ? "Meeting detection is unavailable. Reopen Grounded to retry."
    : discoveryState.meetings.length ? "Choose a meeting to start listening and checking claims."
    : "Join Google Meet in Chrome. Your meeting will appear here.";
}
element("back").onclick = async () => {
  if (!session || !["stopped", "finishing"].includes(session.state) || captureLifecycle.busy || openingHistory) return;
  try {
    await savePresentation();
    if (!historyMode && !session.archived && session.state === "stopped") await bridge.command({ type: "clear", session_id: session.id });
    session = undefined;
    historyMode = false;
    ownsSession = false;
    detectedMeeting = undefined;
    resetResults();
    error();
    await loadHistory();
    buttons();
  } catch (failure) { error(failure.message); }
};

async function loadHistory(append = false) {
  const result = await bridge.command({ type: "history", offset: append ? historyNext : 0 });
  const root = element("history-list");
  if (!append) root.replaceChildren();
  for (const meeting of result.meetings || []) {
    const button = node("button", undefined, "meeting-card history-card");
    const date = new Date(meeting.created_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
    const progress = meeting.state === "finishing" ? " · Finishing checks…" : meeting.finalization?.state === "incomplete" ? " · Checks incomplete" : meeting.finalization?.state === "complete" ? " · Checks complete" : meeting.interrupted ? " · Interrupted" : "";
    button.append(node("strong", meeting.title || "Google Meet"), node("span", `${date} · ${meeting.candidate_count} checks${progress}`, "meeting-url"));
    button.onclick = async () => {
      if (captureLifecycle.busy || openingHistory || session) return;
      openingHistory = true;
      try {
        const saved = await bridge.command({ type: "state", session_id: meeting.id });
        if (!["stopped", "finishing"].includes(saved.state)) throw Error("This meeting is still active. End it before opening its saved view.");
        resetResults();
        historyMode = saved.state === "stopped";
        ownsSession = false;
        session = saved.state === "stopped" ? { ...saved, archived: true } : saved;
        detectedMeeting = undefined;
        restorePending = true;
        saveError = "";
        error();
        render();
      } catch (failure) { error(failure.message); }
      finally { openingHistory = false; buttons(); }
    };
    root.append(button);
  }
  historyNext = result.next_offset ?? null;
  historyRefreshed = Date.now();
  element("history-more").hidden = historyNext === null;
  element("history-empty").hidden = root.children.length > 0;
}
element("history-more").onclick = async () => {
  element("history-more").disabled = true;
  try { await loadHistory(true); }
  catch (failure) { error(failure.message); }
  finally { element("history-more").disabled = false; }
};

function savePresentation() {
  clearTimeout(presentationTimer);
  if (!session || restoringPresentation) return presentationSaving;
  const command = { type: "presentation", session_id: session.id, presentation: {
    expanded: [...cards.items.values()].filter((card) => card.querySelector(".card-details").open).map((card) => card.dataset.id),
    transcript_scroll: Math.round(element("transcript").scrollTop),
    cards_scroll: Math.round(element("candidates").scrollTop),
  } };
  presentationSaving = presentationSaving.catch(() => {}).then(() => bridge.command(command)).then(() => {
    saveError = "";
    buttons();
  }, (failure) => {
    saveError = "Could not save your view. Check the connection before leaving.";
    buttons();
    throw failure;
  });
  return presentationSaving;
}
function presentationChanged() {
  if (!session || restoringPresentation) return;
  clearTimeout(presentationTimer);
  presentationTimer = setTimeout(() => void savePresentation().catch(() => {}), 300);
}
element("candidates").addEventListener("presentationchange", presentationChanged);
element("candidates").addEventListener("scroll", presentationChanged);
element("transcript").addEventListener("scroll", presentationChanged);
window.addEventListener("beforeunload", () => {
  generation++;
  uploads?.stop();
  for (const input of inputs) void input.stop();
});
async function loop() {
  await poll();
  setTimeout(loop, 1000);
}
void loop();
