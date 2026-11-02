import { localOrigin } from "./local-origin.mjs";

const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const actions = new Set(["pause", "resume", "stop", "retry_detection"]);
export const MAX_MEETING_RESPONSE_BYTES = 8 * 1024 * 1024;

async function boundedResponse(response) {
  if (!response.body) throw Error("Empty meeting service response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parts = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_MEETING_RESPONSE_BYTES) {
        await reader.cancel();
        throw Error("Meeting response exceeds the size limit.");
      }
      parts.push(decoder.decode(value, { stream: true }));
    }
    parts.push(decoder.decode());
    return parts.join("");
  } finally {
    reader.releaseLock();
  }
}

export class MeetingClient {
  constructor(origin, transport = fetch) {
    this.origin = localOrigin(origin);
    this.transport = transport;
    this.pending = new Set();
  }
  abort() {
    for (const request of this.pending) request.abort();
    this.pending.clear();
  }
  async request(path, method = "GET", body, headers = {}) {
    const controller = new AbortController();
    this.pending.add(controller);
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await this.transport(this.origin + path, {
        method,
        redirect: "error",
        signal: controller.signal,
        headers: {
          "X-Roomshift-Client": "1",
          ...(body && !(body instanceof ArrayBuffer)
            ? { "Content-Type": "application/json" }
            : {}),
          ...headers,
        },
        body:
          body instanceof ArrayBuffer
            ? body
            : body === undefined
              ? undefined
              : JSON.stringify(body),
      });
      const text = await boundedResponse(response);
      let data;
      try {
        data = JSON.parse(text);
      } catch {
        throw Error("Invalid meeting service response.");
      }
      if (!response.ok)
        throw Error(
          typeof data.error?.message === "string"
            ? `[${/^[A-Z_]+$/.test(data.error.code) ? data.error.code : "SERVICE_ERROR"}] ${data.error.message.slice(0, 400)}`
            : `Meeting service HTTP ${response.status}`,
        );
      return data;
    } catch (error) {
      if (error.name === "AbortError")
        throw Error(
          "Meeting connection timed out or was stopped. Audio is not replayed automatically.",
        );
      throw error;
    } finally {
      clearTimeout(timer);
      this.pending.delete(controller);
    }
  }
  async command(command) {
    if (!command || typeof command !== "object")
      throw Error("Invalid meeting command.");
    const type = command.type;
    if (type === "capabilities")
      return this.request("/api/meetings/capabilities");
    if (type === "history") {
      const offset = command.offset ?? 0;
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > 1000000) throw Error("Invalid history page.");
      return this.request(`/api/meetings?offset=${offset}`);
    }
    if (type === "start") {
      if (
        command.consent !== true ||
        command.audio_consent_version !== "runpod-audio-v2" ||
        typeof command.cloud_detection !== "boolean" ||
        (command.cloud_detection && command.detection_consent_version !== "runpod-kimi-k3-v1") ||
        command.language !== "en"
      )
        throw Error("Explicit collection consent and language are required.");
      return this.request("/api/meetings", "POST", {
        consent: true,
        audio_consent_version: "runpod-audio-v2",
        cloud_detection: command.cloud_detection,
        detection_consent_version: command.cloud_detection ? "runpod-kimi-k3-v1" : null,
        research_enabled: command.research_enabled === true,
        research_consent_version: command.research_enabled === true ? command.research_consent_version : null,
        language: command.language,
        title: command.title || "Google Meet",
      });
    }
    if (!uuid.test(command.session_id))
      throw Error("Invalid meeting identifier.");
    const path = `/api/meetings/${command.session_id}`;
    if (type === "source" && uuid.test(command.candidate_id) && /^S[1-3]$/.test(command.source_id) && Number.isSafeInteger(command.revision) && command.revision > 0)
      return this.request(path + `/candidates/${command.candidate_id}/sources/${command.source_id}?revision=${command.revision}`);
    if (type === "state") return this.request(path);
    if (type === "presentation") {
      return this.request(path + "/presentation", "PATCH", command.presentation);
    }
    if (type === "clear") {
      this.abort();
      return this.request(path, "DELETE");
    }
    if (type === "action" && actions.has(command.action)) {
      if (["pause", "stop"].includes(command.action)) this.abort();
      return this.request(path + "/action", "POST", {
        action: command.action,
        ...(command.action === "stop" && command.finalization_error ? { finalization_error: String(command.finalization_error).slice(0, 400) } : {}),
      });
    }
    if (
      type === "review" &&
      uuid.test(command.candidate_id) &&
      ["edit", "ignore", "retry_research"].includes(command.action)
    ) {
      return this.request(
        path + `/candidates/${command.candidate_id}`,
        "PATCH",
        {
          action: command.action,
          question: command.question ?? null,
        },
      );
    }
    throw Error("Unsupported meeting command.");
  }
  async audio(meta, bytes) {
    if (
      !meta ||
      !uuid.test(meta.session_id) ||
      !["microphone", "system"].includes(meta.source) ||
      !Number.isSafeInteger(meta.epoch) ||
      !Number.isSafeInteger(meta.sequence) ||
      ![meta.start_ms, meta.end_ms, meta.speech_end_ms, meta.age_ms].every(
        Number.isFinite,
      ) ||
      !(bytes instanceof ArrayBuffer) ||
      bytes.byteLength < 3200 ||
      bytes.byteLength > 224000
    )
      throw Error("Invalid PCM upload.");
    return this.request(
      `/api/meetings/${meta.session_id}/audio`,
      "POST",
      bytes,
      {
        "Content-Type": "application/octet-stream",
        "X-Meeting-Epoch": String(meta.epoch),
        "X-Audio-Source": meta.source,
        "X-Audio-Sequence": String(meta.sequence),
        "X-Audio-Start": String(meta.start_ms),
        "X-Audio-End": String(meta.end_ms),
        "X-Audio-Speech-End": String(meta.speech_end_ms),
        "X-Audio-Age": String(meta.age_ms),
        ...(meta.durable === true ? { "X-Audio-Durable": "1" } : {}),
      },
    );
  }

  async audioStatus(meta, digest) {
    if (!uuid.test(meta.session_id) || !["microphone", "system"].includes(meta.source)
      || !Number.isSafeInteger(meta.epoch) || !Number.isSafeInteger(meta.sequence)
      || !/^[a-f0-9]{64}$/.test(digest)) throw Error("Invalid audio receipt identifier.");
    return this.request(`/api/meetings/${meta.session_id}/audio/${meta.epoch}/${meta.source}/${meta.sequence}?sha256=${digest}`);
  }

  async resumeProcessing(sessionId) {
    if (!uuid.test(sessionId)) throw Error("Invalid meeting identifier.");
    return this.request(`/api/meetings/${sessionId}/resume-processing`, "POST");
  }

  async cancelProcessing(sessionId) {
    if (!uuid.test(sessionId)) throw Error("Invalid meeting identifier.");
    return this.request(`/api/meetings/${sessionId}/cancel-processing`, "POST");
  }
}
