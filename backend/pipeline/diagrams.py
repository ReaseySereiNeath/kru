"""The diagram step: Kru draws the summary's diagrams itself.

Small models draw weak diagrams when they write them as part of a long
summary: wrong arrow directions, made-up chapter numbers, too few people. So
after the final summary, Kru asks one focused question per diagram, gets
plain data back (people and relationships, events and chapters, ...),
checks that data, and writes the Mermaid code itself. That means:
- the diagram syntax is always valid,
- every relationship points at a character that is really in the list,
- chapter numbers that can't be right are dropped, not shown,
- colours and layout are the same in every summary.

Where the data comes from:
- Novels: while reading, each chunk's notes list "Relationships" and
  "Key events" (with chapter numbers). Kru collects those lines from every
  chunk, so nothing is lost when notes are merged for long books.
- Learning books: the final summary itself, plus the process steps noted
  while reading.

If anything goes wrong (bad JSON, too little data, the model fails), the
diagram the model wrote in the summary is kept. The diagram step can make a
summary better, never worse.
"""

import logging
import re
from typing import Callable

from app.config import Settings
from pipeline import prompts
from pipeline.book_check import BookCheck, parse_json_reply
from pipeline.extract import ExtractedBook, count_words
from pipeline.llm import LLMClient, LLMError, LLMReply, estimate_tokens, tokens_to_words

log = logging.getLogger("kru.diagrams")

SYSTEM = "You turn notes about a book into JSON. You reply with valid JSON only."

# The headings in the chunk notes that hold diagram material.
RELATIONSHIPS = "Relationships"
KEY_EVENTS = "Key events"

# Line colours in the character map, by kind of relationship.
RELATION_COLOURS = {
    "family": ("#0f6e6a", "teal"),
    "love": ("#c0567b", "pink"),
    "friend": ("#3f7cc0", "blue"),
    "conflict": ("#a8261c", "red"),
    "other": ("#8a94a8", "grey"),
}

MAX_CHARACTERS = 12
MAX_RELATIONSHIPS = 18
MAX_EVENTS = 10


# ---------------------------------------------------------------- reading the chunk notes


def _heading_pattern(name: str) -> re.Pattern:
    # "## Key events", "### Key events:", or "**Key events**"
    return re.compile(rf"^\s*(?:#{{1,6}}\s*|\*\*){re.escape(name)}\b[^\n]*$",
                      re.IGNORECASE | re.MULTILINE)


_ANY_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*\n]+\*\*:?\s*$)", re.MULTILINE)


def pop_section(text: str, name: str) -> tuple[str, str]:
    """Take the section with this heading out of the text: (rest, section body)."""
    match = _heading_pattern(name).search(text)
    if not match:
        return text, ""
    after = text[match.end():]
    nxt = _ANY_HEADING.search(after)
    body = after[: nxt.start()] if nxt else after
    rest = text[: match.start()].rstrip() + ("\n\n" + after[nxt.start():].strip() if nxt else "")
    return rest.strip(), body.strip()


def strip_diagram_sections(notes: str) -> str:
    """The notes without the diagram material (it doesn't belong in the final prompt)."""
    for name in (RELATIONSHIPS, KEY_EVENTS):
        notes, _ = pop_section(notes, name)
    return notes


def section_lines(body: str) -> list[str]:
    """The useful lines of a section: no bullets, no "(Skip if none.)"."""
    lines = []
    for line in body.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+\.)\s+", "", line).strip()
        if not line or line.lower().strip(".()") in ("none", "skip if none", "n/a"):
            continue
        lines.append(line)
    return lines


def collect_lines(all_notes: list[str]) -> tuple[list[str], list[str]]:
    """All relationship lines (without repeats) and all event lines, in book order."""
    relationships, events, seen = [], [], set()
    for notes in all_notes:
        _, body = pop_section(notes, RELATIONSHIPS)
        for line in section_lines(body):
            key = re.sub(r"\W+", " ", line.lower()).strip()
            if key not in seen:
                seen.add(key)
                relationships.append(line)
        _, body = pop_section(notes, KEY_EVENTS)
        events.extend(section_lines(body))
    return relationships, events


def thin_out(lines: list[str], max_words: int) -> list[str]:
    """Keep lines spread evenly over the book until they fit in max_words."""
    total = sum(count_words(line) for line in lines)
    if total <= max_words or len(lines) < 2:
        return lines
    keep = max(2, int(len(lines) * max_words / total))
    step = (len(lines) - 1) / (keep - 1)
    return [lines[round(j * step)] for j in range(keep)]


# ---------------------------------------------------------------- making labels safe for Mermaid


def clean_label(text: object, max_words: int) -> str:
    """Plain words only: quotes, brackets, colons etc. break Mermaid diagrams."""
    # Apostrophes are fine ("Darcy's letter"); double quotes, brackets, colons
    # and a few other characters are not.
    text = re.sub(r"[\"`\[\](){}<>|:;#&*]", " ", str(text))
    words = text.split()
    return " ".join(words[:max_words])


def _norm(name: str) -> str:
    return re.sub(r"\W+", " ", name.lower()).strip()


# ---------------------------------------------------------------- the four diagrams


def character_map(data: dict) -> str | None:
    names = [clean_label(n, 5) for n in data.get("characters") or [] if clean_label(n, 5)]
    names = list(dict.fromkeys(names))[:MAX_CHARACTERS]
    by_norm = {_norm(n): n for n in names}

    def find(name: object) -> str | None:
        """The character in the list this name means, or None."""
        key = _norm(clean_label(name, 5))
        if key in by_norm:
            return by_norm[key]
        # "Darcy" for "Mr. Darcy": accept if exactly one character matches.
        matches = [full for norm, full in by_norm.items()
                   if key and (f" {key} " in f" {norm} " or f" {norm} " in f" {key} ")]
        return matches[0] if len(matches) == 1 else None

    edges, seen = [], set()
    for rel in data.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        a, b = find(rel.get("from")), find(rel.get("to"))
        label = clean_label(rel.get("relation", ""), 4)
        kind = str(rel.get("type", "other")).lower()
        kind = kind if kind in RELATION_COLOURS else "other"
        pair = frozenset((a, b))
        if not a or not b or a == b or not label or pair in seen:
            # A name not in the list, a loop, or a second line between the
            # same two people (one line per pair keeps the map readable).
            continue
        seen.add(pair)
        edges.append((a, label, b, kind))
    edges = edges[:MAX_RELATIONSHIPS]
    if len(edges) < 3:
        return None

    # Only characters that have at least one relationship are drawn.
    used = [n for n in names if any(n in (a, b) for a, _, b, _ in edges)]
    ids = {name: f"C{i + 1}" for i, name in enumerate(used)}
    links = {name: sum(name in (a, b) for a, _, b, _ in edges) for name in used}
    main = sorted(used, key=lambda n: -links[n])[:2]

    lines = ["graph LR"]
    lines += [f'  {ids[n]}["{n}"]' for n in used]
    lines += [f'  {ids[a]} -->|"{label}"| {ids[b]}' for a, label, b, _ in edges]
    for kind, (colour, _) in RELATION_COLOURS.items():
        indexes = [str(i) for i, edge in enumerate(edges) if edge[3] == kind]
        if indexes:
            lines.append(f"  linkStyle {','.join(indexes)} stroke:{colour},stroke-width:2px")
    lines.append("  classDef main fill:#fff3a8,stroke:#14213d,stroke-width:2px")
    lines.append(f"  class {','.join(ids[n] for n in main)} main")

    kinds = [k for k in RELATION_COLOURS if any(e[3] == k for e in edges)]
    legend = ", ".join(f"{RELATION_COLOURS[k][1]} = {k}" for k in kinds)
    return _block(lines) + f"\n\n*Line colours: {legend}. Main characters are highlighted.*"


def plot_timeline(data: dict, chapter_count: int) -> str | None:
    events, last_chapter = [], 0
    for item in (data.get("events") or [])[:MAX_EVENTS]:
        if not isinstance(item, dict):
            continue
        text = clean_label(item.get("event", ""), 12)
        if not text:
            continue
        chapter = item.get("chapter")
        try:
            chapter = int(chapter) if chapter is not None else None
        except (TypeError, ValueError):
            chapter = None
        # A chapter number that can't be right is dropped, not shown: beyond
        # the last chapter, or going backwards in a story told in order.
        if chapter is not None and (chapter < 1 or chapter < last_chapter
                                    or (chapter_count and chapter > chapter_count)):
            chapter = None
        if chapter is not None:
            last_chapter = chapter
        events.append((chapter, text))
    if len(events) < 3:
        return None
    # Drawn top to bottom, one box per event. (Mermaid's own "timeline" puts
    # all events side by side in tiny boxes, which is hard to read, above all
    # on a phone.)
    lines = ["graph TD"]
    for i, (chapter, text) in enumerate(events, start=1):
        label = f"Chapter {chapter}. {text}" if chapter else text
        lines.append(f'  E{i}["{label}"]')
    lines += [f"  E{i} --> E{i + 1}" for i in range(1, len(events))]
    lines.append("  classDef ending fill:#fff3a8,stroke:#14213d,stroke-width:2px")
    lines.append(f"  class E{len(events)} ending")
    return _block(lines)


def concept_map(data: dict) -> str | None:
    core = clean_label(data.get("core", ""), 5)
    concepts = []
    for item in (data.get("concepts") or [])[:8]:
        if not isinstance(item, dict):
            continue
        name = clean_label(item.get("name", ""), 5)
        details = [clean_label(d, 6) for d in (item.get("details") or [])[:3]]
        if name:
            concepts.append((name, [d for d in details if d]))
    if not core or len(concepts) < 3:
        return None
    lines = ["mindmap", f"  root(({core}))"]
    for name, details in concepts:
        lines.append(f"    {name}")
        lines += [f"      {d}" for d in details]
    return _block(lines)


def process_flow(data: dict) -> str | None:
    steps = [clean_label(s, 8) for s in (data.get("steps") or [])[:8]]
    steps = [s for s in steps if s]
    if len(steps) < 3:
        return None
    lines = ["graph TD"]
    lines += [f'  S{i + 1}["{step}"]' for i, step in enumerate(steps)]
    lines += [f"  S{i + 1} --> S{i + 2}" for i in range(len(steps) - 1)]
    if data.get("repeats") is True:
        lines.append(f"  S{len(steps)} --> S1")
    return _block(lines)


def _block(lines: list[str]) -> str:
    return "```mermaid\n" + "\n".join(lines) + "\n```"


# ---------------------------------------------------------------- putting them in the summary


def replace_diagram(markdown: str, heading_words: tuple[str, ...], new_block: str) -> str:
    """Put new_block in the "## ..." section whose heading has one of heading_words.

    The model's own diagram in that section (and a previous legend line) is
    replaced; if it wrote none, the new one is added at the end of the section.
    Sections that don't exist are left alone.
    """
    sections = re.split(r"(?m)^(?=## )", markdown)
    for i, section in enumerate(sections):
        heading = section.split("\n", 1)[0].lower()
        if not heading.startswith("## ") or not any(w in heading for w in heading_words):
            continue
        body = re.sub(r"```mermaid\n.*?```", "", section, count=1, flags=re.DOTALL)
        body = re.sub(r"(?m)^\*Line colours:.*\*$", "", body)  # a legend from an earlier run
        head, _, rest = body.partition("\n")
        rest = rest.strip()
        sections[i] = head + "\n\n" + (rest + "\n\n" if rest else "") + new_block + "\n\n"
        return "".join(sections).rstrip() + "\n"
    return markdown


# Which diagrams each kind of book gets: (request in 4_diagrams.txt, the
# summary headings it belongs under, the function that draws it from the
# data and the book's number of chapters).
FICTION_DIAGRAMS = [
    ("CHARACTER MAP", ("character map",), lambda data, chapters: character_map(data)),
    ("PLOT TIMELINE", ("timeline",), plot_timeline),
]
LEARNING_DIAGRAMS = [
    ("CONCEPT MAP", ("concept map",), lambda data, chapters: concept_map(data)),
    ("PROCESS", ("step by step", "how it works"), lambda data, chapters: process_flow(data)),
]


def request_template(name: str) -> str:
    """The shared top of 4_diagrams.txt plus the one request named `name`."""
    text = prompts.load_prompt(prompts.DIAGRAMS)
    parts = re.split(r"^=== (.+?) ===\s*$", text, flags=re.MULTILINE)
    shared, sections = parts[0], dict(zip(parts[1::2], parts[2::2]))
    if name not in sections:
        raise prompts.PromptError(f"{prompts.DIAGRAMS} has no '=== {name} ===' section")
    return shared.rstrip() + "\n\n" + sections[name].strip() + "\n"


def diagram_notes(check: BookCheck, chunk_notes: list[str], summary: str, room_words: int) -> str:
    relationships, events = collect_lines(chunk_notes)
    if check.book_type == "fiction":
        half = room_words // 2
        return ("Relationships between characters:\n" + "\n".join(thin_out(relationships, half))
                + "\n\nKey events, with chapter numbers:\n" + "\n".join(thin_out(events, half)))
    # Learning: the summary already holds the concepts; the process steps
    # come from the notes. Diagrams the model drew are left out.
    text = re.sub(r"```.*?```", "", summary, flags=re.DOTALL)
    steps = thin_out(events, max(0, room_words - count_words(text)))
    return text.strip() + "\n\nProcess steps noted while reading:\n" + "\n".join(steps)


def add_diagrams(
    markdown: str,
    llm: LLMClient,
    book: ExtractedBook,
    check: BookCheck,
    chunk_notes: list[str],
    cfg: Settings,
    record: Callable[[str, LLMReply], None],
) -> str:
    """Replace the summary's diagrams with ones Kru draws from checked data."""
    plan = FICTION_DIAGRAMS if check.book_type == "fiction" else LEARNING_DIAGRAMS
    for request, headings, draw in plan:
        if not any(re.search(rf"(?im)^## .*{w}", markdown) for w in headings):
            continue  # the summary has no such section (e.g. no process taught)
        try:
            template = request_template(request)
            values = {
                "book_title": book.title,
                "book_type": check.book_type.upper(),
                "chapter_count": len(check.chapters) or "unknown",
            }
            # Fill the notes last, with whatever room the budget leaves.
            empty = prompts.fill(template, {**values, "diagram_notes": ""})
            room = cfg.prompt_budget() - estimate_tokens(SYSTEM) - estimate_tokens(empty)
            notes = diagram_notes(check, chunk_notes, markdown, tokens_to_words(room) - 50)
            prompt = prompts.fill(template, {**values, "diagram_notes": notes})
            data = _ask_json(llm, cfg.llm_model_final, prompt, request, record)
            block = draw(data, len(check.chapters))
        except (LLMError, ValueError) as error:
            log.warning("Kept the model's own %s (%s).", request.lower(), error)
            continue
        if block is None:
            log.warning("Kept the model's own %s (not enough usable data).", request.lower())
            continue
        markdown = replace_diagram(markdown, headings, block)
    return markdown


def _ask_json(llm: LLMClient, model: str, prompt: str, request: str,
              record: Callable[[str, LLMReply], None]) -> dict:
    """Ask for JSON, once more if the first reply isn't valid JSON."""
    for attempt in range(2):
        note = "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
        reply = llm.chat(model, SYSTEM, prompt + (note if attempt else ""), json_mode=True)
        record(f"diagram: {request.lower()}", reply)
        try:
            return parse_json_reply(reply.text)
        except ValueError:
            continue
    raise ValueError("no valid JSON after two tries")
