import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("meeting UI excludes engineering metrics and keeps actionable recovery", async () => {
  const markup = await readFile(new URL("../meeting/index.html", import.meta.url), "utf8");
  const application = await readFile(new URL("../meeting/app.mjs", import.meta.url), "utf8");
  for (const identifier of ["queue-state", "latency", "detector-state", "research-state"]) {
    assert.ok(!markup.includes(`id="${identifier}"`));
    assert.ok(!application.includes(`element("${identifier}")`));
  }
  assert.ok(!application.includes('document.querySelector(".diagnostics").open = true'));
  for (const identifier of ["connection", "review-count", "record-missed", "refresh", "error", "retry", "export", "clear", "consent", "cloud", "setup-panel"]) {
    assert.ok(!markup.includes(`id="${identifier}"`));
    assert.ok(!application.includes(`element("${identifier}")`));
  }
  assert.match(markup, /<title>Grounded<\/title>/);
  assert.ok(!markup.includes("Opening a meeting"));
  assert.ok(!markup.includes('id="start-note"'));
  assert.match(markup, /Detected meetings/);
  assert.match(application, /cloud_detection: true/);
  assert.match(application, /research_enabled: true/);
  const cards = await readFile(new URL("../meeting/cards.mjs", import.meta.url), "utf8");
  for (const label of ["Refine this question", "Helpful", "Not needed", "Missed context"]) {
    assert.ok(!cards.includes(label));
  }
  assert.match(cards, /Claim from the meeting/);
  assert.match(cards, /Research result/);
  assert.match(cards, /No answer yet/);
});
