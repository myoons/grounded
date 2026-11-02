function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

const verdicts = {
  supported: "Supported",
  contradicted: "Contradicted",
  conditional: "Conditional",
  insufficient: "Insufficient evidence",
};
const categories = { capability: "Capability", limits_pricing: "Limits & pricing", policy_licensing: "Policy & licensing", performance_comparison: "Performance", technical_behavior: "Technical behavior", current_status: "Current status" };

export class CandidateCards {
  constructor(root, command, changed, failed, evidence) {
    this.root = root;
    this.command = command;
    this.changed = changed;
    this.failed = failed;
    this.evidence = evidence;
    this.items = new Map();
  }

  clear() {
    this.items.clear();
    this.root.replaceChildren();
    this.session = undefined;
  }

  async act(candidateId, action, values = {}) {
    try {
      await this.command({ type: "review", session_id: this.session.id, candidate_id: candidateId, action, ...values });
      await this.changed();
    } catch (error) {
      this.failed(error.message);
    }
  }

  create(candidate) {
    const card = node("article", undefined, "candidate");
    card.dataset.id = candidate.id;
    const top = node("div", undefined, "card-top");
    top.append(node("span", "", "meta"));
    const dismiss = node("button", "×", "dismiss");
    dismiss.setAttribute("aria-label", "Dismiss this check");
    dismiss.onclick = () => void this.act(candidate.id, "ignore");
    top.append(dismiss);
    const title = node("button", "", "card-title");
    const details = node("details", undefined, "card-details");
    title.setAttribute("aria-expanded", "false");
    title.onclick = () => { details.open = !details.open; };
    details.ontoggle = () => {
      title.setAttribute("aria-expanded", String(details.open));
      if (details.open) {
        for (const other of this.items.values()) {
          if (other !== card) other.querySelector(".card-details").open = false;
        }
      }
      this.root.dispatchEvent(new Event("presentationchange"));
    };
    details.append(node("summary", "Evidence & context"));
    details.append(node("p", "Claim from the meeting", "eyebrow"), node("div", "", "source-count"), node("p", "", "recommendation"), node("h4", "From the conversation"), node("div", "", "evidence"));
    details.append(node("div", "", "citations"), node("ul", "", "caveats"), node("p", "", "conditions"));
    const retry = node("button", "Retry research", "retry-research");
    retry.onclick = () => void this.act(candidate.id, "retry_research");
    retry.hidden = true;
    card.append(top, title, node("p", "", "finding"), retry, details);
    this.root.append(card);
    this.items.set(candidate.id, card);
    return card;
  }

  render(session) {
    this.session = session;
    for (const candidate of session.candidates) {
      const card = this.items.get(candidate.id) || this.create(candidate);
      card.className = `candidate ${candidate.state}`;
      card.hidden = candidate.state === "ignored";
      card.querySelector(".dismiss").hidden = session.archived === true;
      card.querySelector(".card-title").textContent = candidate.statement && candidate.statement !== "Claim awaiting verification" ? candidate.statement : candidate.question;
      card.querySelector(".eyebrow").textContent = categories[candidate.category] || "Claim from the meeting";
      const research = candidate.research || { state: "not_requested" };
      card.dataset.research = research.state;
      card.dataset.verdict = research.verdict || "";
      const ready = research.state === "ready";
      let label = ready ? verdicts[research.verdict] : "Pending";
      let summary = session.research_enabled ? "No research result yet." : "Research is off for this meeting. Enable automatic checks when starting a new meeting.";
      if (["queued", "pending", "searching", "analyzing"].includes(research.state)) {
        label = research.state === "analyzing" ? "Analyzing" : research.state === "searching" ? "Searching" : "Queued";
        summary = "Checking this claim against public sources…";
      } else if (ready) summary = research.summary;
      else if (research.state === "error") { label = "Check failed"; summary = research.message; }
      else if (research.state === "interrupted") { label = "Check paused"; summary = session.state === "stopped" ? "Research stopped when the meeting ended." : "Resume the meeting to continue this check."; }
      else if (research.state === "budget_limit") { label = "Research limit reached"; summary = research.message; }
      if (candidate.state === "deferred") { label = "Not checked · missing context"; summary = `No answer yet. We need more context before researching: ${candidate.missing_information.join("; ") || candidate.reason}`; }
      if (candidate.state === "retracted") { label = "Withdrawn in the conversation"; summary = "This premise is no longer being used. Earlier findings do not apply."; }
      card.querySelector(".meta").textContent = label;
      card.querySelector(".finding").textContent = summary;
      card.querySelector(".finding").setAttribute("aria-label", `Research result: ${summary}`);
      const applicable = ready && !["deferred", "retracted"].includes(candidate.state);
      card.querySelector(".recommendation").textContent = applicable ? `What this means: ${research.recommendation}` : "";
      card.querySelector(".source-count").textContent = applicable ? `${research.citations.length} cited source${research.citations.length === 1 ? "" : "s"}` : "";
      const retry = card.querySelector(".retry-research");
      retry.hidden = session.archived === true || candidate.state !== "candidate" || (!["error", "interrupted"].includes(research.state) && !(ready && research.verdict === "insufficient"));
      retry.disabled = session.state !== "capturing" || !session.research_enabled || candidate.research_attempts >= 3;
      const signature = JSON.stringify([candidate.research_revision, candidate.evidence, candidate.conditions, candidate.missing_information, research]);
      if (card.dataset.detailsSignature !== signature) {
        card.dataset.detailsSignature = signature;
        const evidence = card.querySelector(".evidence");
        evidence.replaceChildren();
        for (const item of candidate.evidence) {
          const time = `${Math.floor(item.start_ms / 60000)}:${String(Math.floor(item.start_ms / 1000) % 60).padStart(2, "0")}`;
          const anchor = node("button", `${item.source === "microphone" ? "My microphone" : "Meeting audio"} · ${time}`, "quote-link");
          anchor.onclick = () => this.evidence(item.id);
          evidence.append(anchor, node("blockquote", item.text));
        }
        const citations = card.querySelector(".citations");
        citations.replaceChildren();
        if (ready && research.citations.length) citations.append(node("h4", "What the sources say"));
        for (const citation of research.citations || []) {
          const source = research.sources.find((item) => item.id === citation.source_id);
          if (!source) continue;
          const link = node("button", `${new URL(source.url).hostname} ↗`, "source-link");
          link.title = source.title;
          const revision = candidate.research_revision;
          link.onclick = async () => {
            try {
              await this.command({ type: "open_source", session_id: this.session.id, candidate_id: candidate.id, source_id: source.id, revision });
            } catch (error) { this.failed(error.message); }
          };
          citations.append(link, node("blockquote", citation.quote));
        }
        const caveats = card.querySelector(".caveats");
        caveats.replaceChildren(...(research.caveats || []).map((text) => node("li", text)));
        card.querySelector(".conditions").textContent = [
          candidate.conditions.length ? `Conditions: ${candidate.conditions.join("; ")}` : "",
          candidate.missing_information.length ? `Missing context: ${candidate.missing_information.join("; ")}` : "",
        ].filter(Boolean).join("\n");
      }
    }
  }
}
