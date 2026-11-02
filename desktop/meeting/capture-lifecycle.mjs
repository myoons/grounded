export class CaptureLifecycle {
  #operation;

  get phase() { return this.#operation?.phase ?? "idle"; }
  get busy() { return Boolean(this.#operation); }

  begin(phase) {
    if (!["starting", "pausing", "ending"].includes(phase)) throw Error("Invalid capture transition.");
    const preempts = phase === "ending" && this.phase !== "ending"
      || phase === "pausing" && this.phase === "starting";
    if (this.busy && !preempts) return undefined;
    const operation = Object.freeze({ phase });
    this.#operation = operation;
    return operation;
  }

  finish(operation) {
    if (!operation || this.#operation !== operation) return false;
    this.#operation = undefined;
    return true;
  }

  owns(operation) { return this.#operation === operation; }
}

export function captureStatus({ phase, state, hasInputs, incomplete = false, archived = false, interrupted = false }) {
  if (archived) return incomplete ? "Checks incomplete" : interrupted ? "Interrupted meeting" : "Saved meeting";
  if (phase === "ending" || state === "finishing") return "Finishing checks…";
  if (phase === "pausing") return "Pausing…";
  if (phase === "starting") return "Starting…";
  if (state === "capturing") return hasInputs ? "Listening" : "Capture stopped · Pending processing";
  if (state === "paused") return "Paused";
  if (state === "stopped") return incomplete ? "Checks incomplete" : "Meeting ended";
  return "Ready to listen";
}
