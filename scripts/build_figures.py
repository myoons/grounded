"""Build original, inspectable SVG figures from code-aligned labels and measured data."""

import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/figures"
COLORS = {
    "ink": "#24313f",
    "muted": "#586777",
    "line": "#ccd6df",
    "paper": "#f5f7fa",
    "blue": "#2563a6",
    "teal": "#087e8b",
    "purple": "#6d55a4",
    "amber": "#a96615",
    "red": "#b34d3d",
}


class Figure:
    def __init__(self, number, name, title, subtitle, caption, height=640):
        self.name, self.title, self.caption = name, title, caption
        self.height = height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="{height}" viewBox="0 0 1080 {height}" role="img" aria-labelledby="title description">',
            f'<title id="title">{escape(title)}</title><desc id="description">{escape(caption)}</desc>',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            "<defs>",
        ]
        for key in ("muted", "blue", "teal", "purple", "amber", "red"):
            self.parts.append(
                f'<marker id="arrow-{key}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0 L8 4 L0 8 Z" fill="{COLORS[key]}"/></marker>'
            )
        self.parts.append('</defs><g font-family="Arial, Helvetica, sans-serif">')
        self.text(
            40, 35, f"GROUNDED / FIGURE {number:02d}", 14, "muted", 700, spacing=2
        )
        self.text(40, 80, title, 30, weight=700)
        self.text(40, 112, subtitle, 17, "muted")
        self.line(40, height - 48, 1040, height - 48, "line", 1)
        self.text(
            40,
            height - 22,
            "Grounded · See Visual Atlas for assumptions, measurement scope and source mapping",
            13,
            "muted",
        )

    def text(
        self, x, y, text, size=18, color="ink", weight=400, spacing=0, anchor="start"
    ):
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{COLORS.get(color, color)}" font-weight="{weight}" letter-spacing="{spacing}" text-anchor="{anchor}">{escape(str(text))}</text>'
        )

    def lines(self, x, y, lines, size=18, color="muted", step=26, weight=400):
        for index, text in enumerate(lines):
            self.text(x, y + index * step, text, size, color, weight)

    def rect(self, x, y, width, height, fill="paper", stroke="line", radius=12):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{COLORS.get(fill, fill)}" stroke="{COLORS.get(stroke, stroke)}" stroke-width="1"/>'
        )

    def box(self, x, y, width, height, title, lines=(), color="blue"):
        self.rect(x, y, width, height, "#ffffff")
        self.parts.append(
            f'<path d="M{x + 16} {y + 18} V{y + height - 18}" stroke="{COLORS[color]}" stroke-width="4"/>'
        )
        self.text(x + 32, y + 34, title, 20, color, 700)
        self.lines(x + 32, y + 66, lines, 17, step=25)

    def line(self, x1, y1, x2, y2, color="muted", width=2, arrow=False, dashed=False):
        marker = f' marker-end="url(#arrow-{color})"' if arrow else ""
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        self.parts.append(
            f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="{COLORS.get(color, color)}" stroke-width="{width}"{marker}{dash}/>'
        )

    def path(self, points, color="muted", dashed=False):
        route = "M" + " L".join(f"{x} {y}" for x, y in points)
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        self.parts.append(
            f'<path d="{route}" fill="none" stroke="{COLORS[color]}" stroke-width="2" marker-end="url(#arrow-{color})"{dash}/>'
        )

    def write(self):
        self.parts.append("</g></svg>")
        (OUTPUT / f"{self.name}.svg").write_text("\n".join(self.parts) + "\n")
        return {"name": self.name, "title": self.title, "caption": self.caption}


def architecture():
    figure = Figure(
        1,
        "architecture",
        "Three places. One continuous conversation.",
        "Capture stays on the participant's Mac. The service owns state. Providers perform heavy work.",
        "Ownership map, not a network topology. The local service may run on the same Mac or a trusted SSH-connected Mac. Whisper is our custom endpoint; Kimi is managed; Firecrawl is external.",
        700,
    )
    for x, title, subtitle in [
        (40, "A  PARTICIPANT MAC", "Permissions + presentation"),
        (390, "B  TRUSTED SERVICE", "Consistency + private storage"),
        (740, "C  PROVIDERS", "Authenticated external calls"),
    ]:
        figure.rect(x, 148, 300, 420)
        figure.text(x + 20, 181, title, 17, weight=700)
        figure.text(x + 20, 208, subtitle, 15, "muted")
    figure.box(
        56,
        235,
        268,
        118,
        "Capture",
        ["Microphone + Core Audio", "16 kHz PCM; bounded chunks"],
    )
    figure.box(
        56,
        390,
        268,
        140,
        "View",
        ["Live script + claim cards", "Past meetings", "No new recording on reopen"],
    )
    figure.box(
        406,
        235,
        268,
        118,
        "Orchestrate",
        ["Epochs, queues, revisions", "Validate before publishing"],
    )
    figure.box(
        406,
        390,
        268,
        140,
        "Remember",
        [
            "Relational SQLite history",
            "Transcript, findings, view",
            "No original audio files",
        ],
        "ink",
    )
    figure.box(756, 235, 268, 89, "Runpod / our GPU", ["Whisper large-v3"], "teal")
    figure.box(
        756,
        345,
        268,
        89,
        "Runpod / public API",
        ["Kimi K3: extract + synthesize"],
        "purple",
    )
    figure.box(756, 455, 268, 89, "Firecrawl", ["Search + readable sources"], "amber")
    figure.line(324, 284, 406, 284, "blue", arrow=True)
    figure.line(674, 284, 710, 284, "muted")
    figure.line(710, 284, 710, 500, "muted")
    figure.line(710, 284, 756, 284, "teal", arrow=True)
    figure.line(710, 390, 756, 390, "purple", arrow=True)
    figure.line(710, 500, 756, 500, "amber", arrow=True)
    figure.line(406, 448, 324, 448, "blue", arrow=True)
    figure.text(335, 438, "Saved", 14, "blue")
    figure.path([(406, 328), (365, 328), (365, 372), (190, 372), (190, 390)], "blue")
    figure.text(260, 365, "Live", 14, "blue")
    figure.line(540, 353, 540, 390, "blue", arrow=True)
    figure.text(40, 610, "Localhost or SSH", 19, "blue", 700)
    figure.text(300, 610, "Keys remain server-side", 19, "ink", 700)
    figure.text(665, 610, "No public multi-user API", 19, "amber", 700)
    return figure.write()


def provenance():
    figure = Figure(
        2,
        "provenance",
        "A finding keeps its chain of evidence.",
        "An illustrative SQLite claim, with shortened IDs for readability.",
        "A concrete example: utterance U17 links to claim C4, retrieved source S1 and exact quote S1-Q2. These are illustrative labels, not actual API UUIDs. A quote establishes provenance, not infallible interpretation.",
        680,
    )
    figure.box(
        40,
        155,
        470,
        170,
        "01  Utterance · U17",
        [
            "SQLite WAL works across servers",
            "on a network filesystem.",
            "Input channel + timestamp + segment ID",
        ],
        "blue",
    )
    figure.box(
        570,
        155,
        470,
        170,
        "02  Claim · C4 / revision 2",
        [
            "Question: does WAL support this setup?",
            "Category: technical behavior",
            "Evidence reference: U17",
        ],
        "purple",
    )
    figure.box(
        570,
        380,
        470,
        185,
        "03  Source · S1 / quote S1-Q2",
        [
            "sqlite.org/wal.html",
            "WAL does not work over a network",
            "filesystem.",
            "Exact text + server-owned source mapping",
        ],
        "amber",
    )
    figure.box(
        40,
        380,
        470,
        185,
        "04  Finding · contradicted",
        [
            "No: WAL does not support this setup.",
            "Inspect the source; jump back to U17.",
            "Publication requires matching revision.",
            "The model never supplies a trusted URL.",
        ],
        "blue",
    )
    figure.line(510, 235, 570, 235, "purple", arrow=True)
    figure.line(805, 325, 805, 380, "amber", arrow=True)
    figure.line(570, 465, 510, 465, "blue", arrow=True)
    figure.text(
        40,
        608,
        "Read the answer → inspect the quotation → locate the original speech.",
        19,
        weight=700,
    )
    return figure.write()


def concurrency():
    figure = Figure(
        3,
        "concurrency",
        "Listening does not wait for research.",
        "Schematic execution lanes: event order only, not a measured timeline.",
        "Audio capture continues while ordered ASR, single-flight claim extraction and two independent research workers operate. Bar widths do not encode measured durations. Queues are bounded; failure handling is explicit.",
        670,
    )
    for y, label in [
        (185, "Capture"),
        (265, "ASR"),
        (345, "Extraction"),
        (425, "Research A"),
        (505, "Research B"),
    ]:
        figure.text(40, y + 12, label, 21, weight=700)
        figure.line(250, y + 16, 1030, y + 16, "line", 1)
    figure.line(250, 196, 1020, 196, "blue", 7, arrow=True)
    figure.text(450, 178, "Authorized audio keeps arriving", 18, "blue")
    for x, label in [
        (250, "Chunk 1"),
        (425, "Chunk 2"),
        (600, "Chunk 3"),
        (775, "Chunk 4"),
    ]:
        figure.rect(x, 246, 155, 44, "#e8f4f5", "#e8f4f5", 6)
        figure.text(x + 16, 274, label, 18, "teal", 700)
    for x, width, label in [
        (425, 225, "Snapshot 1"),
        (710, 310, "Latest pending snapshot"),
    ]:
        figure.rect(x, 326, width, 44, "#f1edf8", "#f1edf8", 6)
        figure.text(x + 16, 354, label, 17, "purple", 700)
    for x, y, width, label in [
        (680, 406, 340, "Claim A: search + synthesize"),
        (710, 486, 310, "Claim B: search + synthesize"),
    ]:
        figure.rect(x, y, width, 44, "#fbf2e5", "#fbf2e5", 6)
        figure.text(x + 16, y + 28, label, 17, "amber", 700)
    figure.line(250, 572, 1030, 572, "muted", arrow=True)
    figure.text(250, 600, "Progress through the meeting", 17, "muted")
    return figure.write()


def versions():
    figure = Figure(
        4,
        "versions",
        "Old work cannot overwrite new state.",
        "Two guards protect different things: capture epoch and claim revision.",
        "Schematic races after successful generation changes. Pause must first drain retained audio; failed drain preserves the epoch for recovery. After epoch advance stale uploads are rejected. Claim revision changes reject obsolete research results.",
        690,
    )
    for y, letter, title, first, change, current, late in [
        (
            155,
            "A",
            "Capture epoch",
            "Audio begins · epoch 7",
            "Pause",
            "Current epoch 8",
            "Late audio · epoch 7",
        ),
        (
            370,
            "B",
            "Claim revision",
            "Research begins · rev 2",
            "Scope changes",
            "Current revision 3",
            "Late result · rev 2",
        ),
    ]:
        figure.rect(40, y, 1000, 190)
        figure.text(60, y + 33, f"{letter}  {title}", 21, weight=700)
        figure.text(70, y + 80, first, 19, "blue", 700)
        figure.line(340, y + 74, 480, y + 74, "muted", arrow=True)
        figure.text(490, y + 80, change, 18, "amber", 700)
        figure.line(655, y + 74, 735, y + 74, "muted", arrow=True)
        figure.text(750, y + 80, current, 19, "blue", 700)
        figure.path([(190, y + 103), (190, y + 145), (750, y + 145)], "red", True)
        figure.text(360, y + 135, late, 17, "red")
        figure.text(775, y + 151, "REJECT / DISCARD", 18, "red", 700)
    figure.text(
        40,
        604,
        "Publish only when session ID, epoch, claim revision and state still match.",
        20,
        weight=700,
    )
    return figure.write()


def lifecycle():
    figure = Figure(
        5,
        "lifecycle",
        "End the meeting, not its history.",
        "Runtime states and saved viewing are deliberately different concepts.",
        "Capture ends before processing ends. Finishing drains accepted audio, extraction and research. Interrupted work requires explicit recovery; opening history never starts recording.",
        690,
    )
    figure.text(40, 163, "RUNTIME", 15, "muted", 700, spacing=2)
    figure.box(40, 195, 280, 116, "Capturing", ["Audio + checks are active"], "blue")
    figure.box(400, 195, 280, 116, "Paused", ["Fresh inputs on Resume"], "amber")
    figure.box(760, 195, 280, 116, "Finishing", ["Drain accepted work"], "purple")
    figure.line(320, 226, 400, 226, "amber", arrow=True)
    figure.text(330, 215, "Pause", 15, "amber")
    figure.line(400, 287, 320, 287, "blue", arrow=True)
    figure.text(324, 278, "Resume", 15, "blue")
    figure.line(680, 250, 760, 250, "muted", arrow=True)
    figure.text(700, 238, "End", 15)
    figure.path([(180, 311), (180, 350), (900, 350), (900, 311)], "muted")
    figure.text(445, 342, "End / Meet departure", 17, "muted")
    figure.box(
        40,
        415,
        460,
        140,
        "Stopped / saved history",
        [
            "Completed or explicitly incomplete",
            "Relational checkpoints + view state",
        ],
        "ink",
    )
    figure.box(
        600,
        415,
        440,
        140,
        "Past meetings",
        ["Open without capture or new requests", "Explicitly recover interrupted work"],
        "blue",
    )
    figure.line(500, 480, 600, 480, "blue", arrow=True)
    figure.text(507, 462, "Read", 17, "blue")
    figure.path([(900, 311), (900, 385), (270, 385), (270, 415)], "teal")
    figure.text(410, 377, "Drain or mark incomplete", 16, "teal")
    figure.text(
        40,
        608,
        "Returning to the list does not cancel finishing or delete committed results.",
        19,
        weight=700,
    )
    return figure.write()


def persistence():
    figure = Figure(
        6,
        "persistence",
        "Relationships survive; partial commits do not.",
        "Fourteen tables: thirteen domain tables plus durable audio receipts.",
        "A grouped relational map, not every foreign key. Meeting-scoped composite keys link immutable transcript rows to claim revisions, research and citations. Audio outcome and receipt commit atomically before local deletion.",
        830,
    )
    nodes = [
        (40, 170, "meetings", ["Lifecycle / epoch / processing cursor"], "blue"),
        (40, 310, "transcript_segments", ["Original text + stable sequence"], "blue"),
        (400, 170, "claims", ["Stable identity / current revision"], "purple"),
        (400, 310, "claim_revisions", ["statement / question / category"], "purple"),
        (760, 170, "meeting_presentation", ["expanded_claims"], "blue"),
        (
            760,
            310,
            "research_results",
            ["research_sources", "research_citations / caveats"],
            "amber",
        ),
        (
            400,
            490,
            "claim_evidence",
            ["Revision to original utterance", "claim_attributes / relations"],
            "purple",
        ),
        (
            40,
            490,
            "audio_receipts",
            ["Chunk identity + terminal outcome", "Same commit as transcript"],
            "teal",
        ),
    ]
    for x, y, title, body, color in nodes:
        figure.box(x, y, 280, 112, title, body, color)
    figure.line(320, 212, 400, 212, "purple", arrow=True)
    figure.line(680, 212, 760, 212, "blue", arrow=True)
    figure.line(180, 282, 180, 310, "blue", arrow=True)
    figure.line(540, 282, 540, 310, "purple", arrow=True)
    figure.line(680, 366, 760, 366, "amber", arrow=True)
    figure.line(540, 422, 540, 490, "purple", arrow=True)
    figure.path([(400, 545), (355, 545), (355, 366), (320, 366)], "purple")
    figure.line(180, 422, 180, 490, "teal", dashed=True)
    figure.text(760, 500, "Composite ownership keys", 20, weight=700)
    figure.lines(
        760, 534, ["meeting + claim + revision", "prevent cross-version citations."], 16
    )
    figure.rect(40, 650, 1000, 100, "#edf4f9", "#edf4f9")
    figure.text(62, 682, "COMMIT", 17, "teal", 700)
    figure.text(220, 682, "Transcript + receipt become durable together.", 19)
    figure.text(62, 721, "ROLLBACK", 17, "red", 700)
    figure.text(
        220, 721, "Prior rows remain; no acknowledgement authorizes audio deletion.", 17
    )
    return figure.write()


def boundaries():
    figure = Figure(
        7,
        "boundaries",
        "Model output is a proposal, not authority.",
        "Server-side gates control what becomes a visible finding.",
        "The server validates transcript references, source-specific quote IDs, exact quotation spans and current session/claim versions. These gates constrain provenance and shape, not the semantic truth of all model interpretations.",
        690,
    )
    steps = [
        ("1", "Known transcript IDs", "Do not invent the utterance."),
        ("2", "Retrieved quote options", "The server owns source IDs and URLs."),
        ("3", "Schema + exact-span checks", "Reject unknown or fabricated citations."),
        ("4", "Current-state checks", "Session, epoch and revision must match."),
    ]
    for index, (number, title, body) in enumerate(steps):
        x = 40 + (index % 2) * 520
        y = 155 + (index // 2) * 170
        figure.box(
            x,
            y,
            480,
            132,
            f"{number}  {title}",
            [body],
            "purple" if index < 2 else "blue",
        )
    figure.rect(40, 510, 1000, 110, "#fbf2e5", "#fbf2e5")
    figure.text(64, 545, "NO READABLE EVIDENCE ≠ FALSE", 22, "amber", 700)
    figure.text(
        64,
        581,
        "Return insufficient evidence. A valid quote still does not prove a valid interpretation.",
        18,
        "ink",
    )
    return figure.write()


def recovery():
    figure = Figure(
        8,
        "recovery",
        "Failures stop at the narrowest safe boundary.",
        "Six small multiples: failure → preserved behavior → explicit recovery.",
        "Research failures leave transcription running. Durable encrypted audio survives processing backlog; local storage exhaustion stops new intake without evicting retained chunks. Explicit recovery resumes interrupted work. These are mechanisms, not measured reliability percentages.",
        750,
    )
    rows = [
        (
            "Audio backlog",
            "Keep encrypted saved chunks.",
            "Retry bounded processing.",
            "blue",
        ),
        (
            "Detector failure",
            "Keep the transcript flowing.",
            "Use Retry checks.",
            "purple",
        ),
        (
            "Search failure",
            "Mark only that card failed.",
            "Retry that research job.",
            "amber",
        ),
        (
            "No useful sources",
            "Show insufficient evidence.",
            "Inspect context or retry.",
            "amber",
        ),
        (
            "Storage failure",
            "Keep last committed history.",
            "Fix disk; retry saving.",
            "ink",
        ),
        (
            "Service restart",
            "Committed rows remain.",
            "Explicitly resume pending work.",
            "blue",
        ),
    ]
    for index, (title, behavior, action, color) in enumerate(rows):
        x = 40 + (index % 2) * 520
        y = 150 + (index // 2) * 170
        figure.box(x, y, 480, 145, title, [behavior, action], color)
    figure.text(
        40,
        685,
        "An error is not a verdict. A retry is not permission to replay old audio.",
        19,
        weight=700,
    )
    return figure.write()


def latency():
    data = json.loads((OUTPUT / "measurements.json").read_text())
    figure = Figure(
        9,
        "latency",
        "In this run, synthesis dominated research time.",
        "Real Firecrawl + Kimi requests; six synthetic claims; one observation per category.",
        data["scope"]
        + " Shared zero baseline, seconds. Search and synthesis are stacked; total labels include negligible validation/rounding overhead. Source hashes and values are in measurements.json.",
        730,
    )
    figure.rect(250, 145, 16, 16, "amber", "amber", 0)
    figure.text(276, 159, "Search", 17, "amber", 700)
    figure.rect(400, 145, 16, 16, "purple", "purple", 0)
    figure.text(426, 159, "Synthesis", 17, "purple", 700)
    scale = 23
    for tick in range(0, 31, 5):
        x = 250 + tick * scale
        figure.line(x, 185, x, 578, "line", 1)
        figure.text(x, 606, str(tick), 17, "muted", anchor="middle")
    for index, row in enumerate(data["rows"]):
        y = 202 + index * 62
        search = row["search_ms"] / 1000 * scale
        synthesis = row["synthesis_ms"] / 1000 * scale
        figure.text(40, y + 25, row["label"], 18)
        figure.rect(250, y, search, 36, "amber", "amber", 0)
        figure.rect(250 + search, y, synthesis, 36, "purple", "purple", 0)
        figure.text(
            263 + search + synthesis,
            y + 25,
            f"{row['total_ms'] / 1000:.2f} s",
            18,
            weight=700,
        )
    figure.text(
        250, 636, "Research duration (seconds) · zero-based common scale", 17, "muted"
    )
    figure.text(
        40,
        675,
        "Not end-to-end latency. No percentiles, error bars or speedup claim from n = 1 per case.",
        17,
        "muted",
    )
    return figure.write()


def evidence():
    figure = Figure(
        10,
        "evidence-matrix",
        "Evidence has scope, not a single score.",
        "Read the tested boundary and the remaining gap together.",
        "Automated invariants, real-provider synthetic runs, persistence checks and resource experiments establish different properties. Their counts are not a combined accuracy or reliability percentage.",
        760,
    )
    columns = [(40, "EVIDENCE"), (360, "ESTABLISHES"), (710, "DOES NOT ESTABLISH")]
    for x, label in columns:
        figure.text(x, 164, label, 15, "muted", 700, spacing=1)
    rows = [
        (
            "Unit + failure tests",
            "Boundaries and invariants",
            "Natural speech accuracy",
        ),
        (
            "Real-provider fixtures",
            "ASR → research → finding",
            "Every accent or workload",
        ),
        (
            "SQLite + UI restart",
            "Saved result + links reopen",
            "Arbitrary hardware recovery",
        ),
        (
            "Startup / End fixtures",
            "Late inputs close safely",
            "All OS permission behavior",
        ),
        (
            "Capacity stress fixture",
            "Measured bounded sample",
            "Unlimited meeting length",
        ),
        (
            "Speech boundary samples",
            "Timing / context trade-offs",
            "General word accuracy",
        ),
    ]
    for index, values in enumerate(rows):
        y = 210 + index * 66
        figure.line(40, y - 24, 1040, y - 24, "line", 1)
        for column, value in enumerate(values):
            figure.text(
                columns[column][0],
                y + 9,
                value,
                18,
                "ink" if column < 2 else "muted",
                700 if column == 0 else 400,
            )
    figure.rect(40, 636, 1000, 64, "#edf4f9", "#edf4f9")
    figure.text(
        62,
        676,
        "Validation separates deterministic behavior, model quality and device-specific acceptance.",
        19,
        "blue",
        700,
    )
    return figure.write()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    figures = [
        build()
        for build in (
            architecture,
            provenance,
            concurrency,
            versions,
            lifecycle,
            persistence,
            boundaries,
            recovery,
            latency,
            evidence,
        )
    ]
    (OUTPUT / "manifest.json").write_text(json.dumps(figures, indent=2) + "\n")
    sections = "\n".join(
        f'<section id="{item["name"]}"><img src="{item["name"]}.svg" alt="{escape(item["caption"])}"/><p>{escape(item["caption"])}</p></section>'
        for item in figures
    )
    illustrations = (
        (
            "conversation-to-evidence",
            "Information changes form while listening continues.",
        ),
        (
            "stale-result-gates",
            "Obsolete epochs and revisions are rejected, not transformed into current work.",
        ),
    )
    sections = (
        "\n".join(
            f'<section><img src="{name}.png" alt="{escape(caption)}"/><p>{escape(caption)} Conceptual illustration, not a runtime screenshot or measured trace.</p></section>'
            for name, caption in illustrations
        )
        + "<h2>Technical reference and measurements</h2>"
        + sections
    )
    links = " ".join(
        f'<a href="#{item["name"]}">{index + 1:02d} {escape(item["name"].replace("-", " "))}</a>'
        for index, item in enumerate(figures)
    )
    gallery = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Grounded — Visual Atlas</title><style>body{{margin:0;background:#f5f7fa;color:#24313f;font-family:Arial,Helvetica,sans-serif}}header,main{{max-width:1080px;margin:auto;padding:28px}}h1{{font-size:36px;letter-spacing:-1px}}nav{{display:flex;gap:14px;flex-wrap:wrap}}a{{color:#2563a6}}section{{margin:28px 0 60px}}img{{display:block;width:100%;height:auto;background:white}}p{{font-size:17px;line-height:1.6;max-width:900px}}@media(max-width:600px){{header,main{{padding:16px}}h1{{font-size:28px}}section{{overflow-x:auto}}img{{min-width:780px}}}}@media print{{body{{background:white}}header{{display:none}}section{{break-after:page;margin:0}}main{{padding:0}}}}</style></head><body><header><h1>Grounded / Visual Atlas</h1><p>Ten questions. Ten original figures. Static schematics are labeled; measurements retain their limits.</p><nav>{links}</nav></header><main>{sections}</main></body></html>'
    (OUTPUT / "index.html").write_text(gallery + "\n")
    print(f"Generated {len(figures)} original figures and a local gallery.")


if __name__ == "__main__":
    main()
