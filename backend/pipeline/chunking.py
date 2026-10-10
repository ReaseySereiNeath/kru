"""Split an extracted book into chunks of about CHUNK_TARGET_WORDS words.

Each chunk is summarized by one model call, so a chunk must be small enough
to fit in the model's context, and should follow chapter boundaries where
possible so each set of notes covers whole chapters.

How we find chapters, best source first:
1. The file's own structure (PDF bookmarks, EPUB table of contents).
2. Chapter titles from the book check (Phase 3), found as headings in the text.
3. Nothing found: the whole book is split by paragraphs.

Then:
- Sections listed in skip_sections (e.g. "Index") are removed.
- Short chapters are combined into one chunk.
- Long chapters are split into roughly equal parts at paragraph breaks.
"""

import math
import re
from dataclasses import dataclass

from pipeline.extract import ExtractedBook, Section, count_words

# A chunk may go this much over the target to keep a chapter whole
# (better one 4,500-word chunk than a 4,000 and a 500).
OVERFLOW = 1.2
# A heading is a short paragraph. Longer ones are never treated as headings.
MAX_HEADING_WORDS = 15
# Chapter titles that appear this close together are a table of contents
# printed in the book, not real chapter starts.
LISTING_GAP_WORDS = 30


@dataclass
class Chunk:
    index: int  # 0-based position in the book
    chapter_titles: list[str]
    text: str
    word_count: int


@dataclass
class ChunkPlan:
    method: str  # "file structure", "chapter titles", or "paragraphs"
    chunks: list[Chunk]


def normalize(text: str) -> str:
    """Lowercase, with punctuation turned into spaces: 'Chapter 1: Hi!' -> 'chapter 1 hi'."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def plan_chunks(
    book: ExtractedBook,
    target_words: int,
    chapter_titles: list[str] | None = None,
    skip_sections: list[str] | None = None,
) -> ChunkPlan:
    if book.has_structure:
        method, sections = "file structure", book.sections
    else:
        sections = split_by_titles(book.paragraphs, chapter_titles or [])
        if sections:
            method = "chapter titles"
        else:
            method, sections = "paragraphs", [Section("", book.paragraphs)]
    sections = remove_skipped(sections, skip_sections or [])
    return ChunkPlan(method, make_chunks(sections, target_words))


# ---------------------------------------------------------------- skipping


def remove_skipped(sections: list[Section], skip_sections: list[str]) -> list[Section]:
    """Drop sections whose title matches one in skip_sections (ignoring case and punctuation)."""
    skip = {normalize(s) for s in skip_sections if normalize(s)}
    return [s for s in sections if not (s.title and normalize(s.title) in skip)]


# ---------------------------------------------------------------- finding chapters by title


def is_heading_for(paragraph: str, title: str) -> bool:
    """Does this paragraph look like the heading for this chapter title?

    Books and tables of contents often write titles a little differently, so
    besides an exact match we accept:
      title "The Storm",            heading "Chapter 1: The Storm"
      title "Chapter 1: The Storm", heading "Chapter 1"  or  "The Storm"
    """
    p, t = normalize(paragraph), normalize(title)
    if not p or not t or len(p.split()) > MAX_HEADING_WORDS:
        return False
    if p == t:
        return True
    if p.endswith(" " + t):
        return True
    # Only part of the title is on this line; require at least two words so a
    # line like "Storm!" doesn't count.
    return len(p.split()) >= 2 and (t.startswith(p + " ") or t.endswith(" " + p))


def split_by_titles(paragraphs: list[str], titles: list[str]) -> list[Section] | None:
    """Split paragraphs into chapters by finding each title as a heading.

    Returns None if fewer than two titles were found (not worth trusting).
    """
    if len(titles) < 2:
        return None

    # 1. Every place any title appears as a heading: (paragraph index, title index).
    matches = [
        (pi, ti)
        for pi, para in enumerate(paragraphs)
        for ti, title in enumerate(titles)
        if is_heading_for(para, title)
    ]

    # 2. Ignore the book's printed table of contents: a run of titles, in
    #    order, with almost no text between them. We keep only the last
    #    match of such a run, because that one may be followed by real text
    #    (e.g. a "Part One" heading directly followed by "Chapter 1").
    words_before = [0]  # words_before[i] = words in paragraphs[:i]
    for para in paragraphs:
        words_before.append(words_before[-1] + count_words(para))

    def gap(a: int, b: int) -> int:  # words strictly between paragraphs a < b
        return words_before[b] - words_before[a + 1]

    runs: list[list[tuple[int, int]]] = []
    for pi, ti in matches:
        if runs:
            prev_pi, prev_ti = runs[-1][-1]
            if pi == prev_pi:  # one line matching two titles: keep the first
                continue
            if ti > prev_ti and gap(prev_pi, pi) < LISTING_GAP_WORDS:
                runs[-1].append((pi, ti))
                continue
        runs.append([(pi, ti)])
    real = [run[-1] for run in runs]

    # 3. Walk the titles in order, taking the first heading after the previous
    #    chapter's start. Titles never found are left inside the previous chapter.
    starts: list[tuple[int, str]] = []
    last_pi = -1
    for ti, title in enumerate(titles):
        found = next((pi for pi, mti in real if mti == ti and pi > last_pi), None)
        if found is not None:
            starts.append((found, title))
            last_pi = found
    if len(starts) < 2:
        return None

    sections = []
    if starts[0][0] > 0:
        sections.append(Section("", paragraphs[: starts[0][0]]))
    for k, (pi, title) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(paragraphs)
        sections.append(Section(title, paragraphs[pi:end]))
    return sections


# ---------------------------------------------------------------- building chunks


def make_chunks(sections: list[Section], target_words: int) -> list[Chunk]:
    limit = target_words * OVERFLOW

    # 1. Turn sections into "pieces" no bigger than about the target:
    #    a short chapter is one piece; a long one is cut into equal parts.
    pieces: list[tuple[str, list[str]]] = []  # (label, paragraphs)
    for section in sections:
        paragraphs = split_long_paragraphs(section.paragraphs, target_words // 2)
        words = sum(count_words(p) for p in paragraphs)
        if words == 0:
            continue
        if words <= limit:
            pieces.append((section.title, paragraphs))
            continue
        parts = split_evenly(paragraphs, math.ceil(words / target_words))
        for k, part in enumerate(parts, start=1):
            label = f"{section.title} (part {k} of {len(parts)})" if section.title else ""
            pieces.append((label, part))

    # 2. Pack pieces into chunks, in order, starting a new chunk when the next
    #    piece would push the current one over the limit.
    chunks: list[Chunk] = []
    current: list[tuple[str, list[str]]] = []
    current_words = 0
    for label, paragraphs in pieces:
        words = sum(count_words(p) for p in paragraphs)
        if current and current_words + words > limit:
            chunks.append(_build_chunk(len(chunks), current))
            current, current_words = [], 0
        current.append((label, paragraphs))
        current_words += words
    if current:
        chunks.append(_build_chunk(len(chunks), current))
    return chunks


def _build_chunk(index: int, pieces: list[tuple[str, list[str]]]) -> Chunk:
    blocks = []
    for label, paragraphs in pieces:
        # Mark where each chapter starts, so the model sees the boundaries.
        # Skip it if the text already starts with that heading.
        if label and normalize(paragraphs[0]) != normalize(label):
            blocks.append(f"## {label}")
        blocks.extend(paragraphs)
    return Chunk(
        index=index,
        chapter_titles=[label for label, _ in pieces if label],
        text="\n\n".join(blocks),
        word_count=sum(count_words(p) for _, paragraphs in pieces for p in paragraphs),
    )


def split_evenly(paragraphs: list[str], n: int) -> list[list[str]]:
    """Cut paragraphs into n parts of about equal word count, at paragraph breaks."""
    total = sum(count_words(p) for p in paragraphs)
    size = total / n
    parts: list[list[str]] = [[]]
    done = 0
    for para in paragraphs:
        words = count_words(para)
        # Start the next part when this paragraph's middle would cross the
        # boundary, so each break lands as close to the ideal point as possible.
        if parts[-1] and len(parts) < n and done + words / 2 > size * len(parts):
            parts.append([])
        parts[-1].append(para)
        done += words
    return parts


def split_long_paragraphs(paragraphs: list[str], max_words: int) -> list[str]:
    """Break any paragraph over max_words into smaller pieces at sentence ends.

    Rare in real books, but it happens with badly extracted PDFs or text
    files with no line breaks. Without this, one giant "paragraph" could make
    a chunk too big for the model.
    """
    out = []
    for para in paragraphs:
        if count_words(para) <= max_words:
            out.append(para)
            continue
        current: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            words = sentence.split()
            # A single sentence longer than the limit is cut by word count.
            for start in range(0, len(words), max_words):
                piece = words[start : start + max_words]
                if current and len(current) + len(piece) > max_words:
                    out.append(" ".join(current))
                    current = []
                current.extend(piece)
        if current:
            out.append(" ".join(current))
    return out


def split_oversized(chunks: list[Chunk], max_words: int) -> list[Chunk]:
    """Split any chunk with more than max_words words into smaller chunks.

    This is the context safety net: the summary step works out how many
    words fit in the model's context, and no chunk may be bigger. With the
    default settings it never triggers, but it does if CHUNK_TARGET_WORDS
    is set high or LLM_CONTEXT_TOKENS low. Chunks are renumbered afterwards.
    """
    out: list[Chunk] = []
    for chunk in chunks:
        if count_words(chunk.text) <= max_words:
            out.append(chunk)
            continue
        paragraphs = split_long_paragraphs(chunk.text.split("\n\n"), max_words // 2)
        n = 2
        while True:  # always ends: at worst every paragraph is its own part
            parts = split_evenly(paragraphs, n)
            if all(sum(count_words(p) for p in part) <= max_words for part in parts):
                break
            n += 1
        for part in parts:
            body = [p for p in part if not p.startswith("## ")]  # don't count our headings
            # Every part keeps the chunk's chapter titles: we don't track
            # which chapter each paragraph came from.
            out.append(Chunk(0, chunk.chapter_titles, "\n\n".join(part),
                             sum(count_words(p) for p in body)))
    for i, chunk in enumerate(out):
        chunk.index = i
    return out
