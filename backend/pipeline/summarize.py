"""The summary pipeline: book file -> final summary in Markdown.

Steps (the names match the job statuses used from Phase 4):
1. extracting   read the file, count words, check the limits
2. analyzing    the book check: book type, subject, chapters, sections to skip
3. summarizing  notes for each chunk, in order, passing each chunk's
                "Carryover" on to the next one
4. merging      only if all the notes are too long for the final prompt:
                combine groups of notes into shorter notes, until they fit
5. finalizing   the final summary

Progress is saved in a store after every model call, so if the run stops,
the next run continues from the last finished chunk.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config import Settings, settings
from pipeline import prompts
from pipeline.book_check import BookCheck, run_book_check
from pipeline.chunking import Chunk, plan_chunks, split_evenly, split_oversized
from pipeline.extract import ExtractedBook, book_status, check_file_size, extract_book
from pipeline.llm import LLMClient, LLMError, LLMReply, estimate_tokens, tokens_to_words

# The carryover passed to the next chunk is cut to this many words. The prompt
# asks for under 80, but models sometimes write more, and we must know its
# maximum size to be sure the next prompt fits.
CARRYOVER_MAX_WORDS = 150
# If a reply has no "Carryover" section, use the start of its notes instead.
FALLBACK_CARRYOVER_WORDS = 100
# Room kept for the "Chapter(s) in this chunk" line when sizing chunks.
CHAPTER_TITLES_ALLOWANCE = "x " * 80
# Never make chunks smaller than this to fit the context; something is wrong.
MIN_CHUNK_WORDS = 500

REJECTED_MESSAGES = {
    "too_long": "This book is too long. Kru handles books up to {max_words:,} words (about 500 pages).",
    "scanned_not_supported": (
        "This PDF looks scanned (its pages are pictures of text). "
        "Scanned books are not supported yet."
    ),
}


class BookRejected(Exception):
    """The book can't be summarized (too long, scanned). Not worth retrying."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Progress:
    status: str  # extracting, analyzing, summarizing, merging, finalizing, done
    current: int = 0
    total: int = 0
    message: str = ""


@dataclass
class NoteBlock:
    """Notes covering parts first..last of the book (1-based chunk numbers)."""
    first: int
    last: int
    titles: list[str] = field(default_factory=list)
    text: str = ""

    def render(self) -> str:
        if self.first == self.last:
            header = f"### Part {self.first}"
        else:
            header = f"### Parts {self.first}-{self.last}"
        if self.titles:
            header += f" ({_short_titles(self.titles)})"
        return f"{header}\n\n{self.text}"


def render_blocks(blocks: list[NoteBlock]) -> str:
    return "\n\n".join(b.render() for b in blocks)


# ---------------------------------------------------------------- the pipeline


def summarize_book(
    path: str | Path,
    llm: LLMClient,
    store,
    cfg: Settings = settings,
    on_progress: Callable[[Progress], None] = lambda p: None,
) -> str:
    """Summarize the book at `path` and return the Markdown summary."""
    if store.summary:
        on_progress(Progress("done", message="Already summarized"))
        return store.summary["markdown"]

    # 1. Extract
    on_progress(Progress("extracting", message="Reading the book"))
    path = Path(path)
    check_file_size(path, cfg.max_file_mb)
    book = extract_book(path)
    status = book_status(book, cfg.max_words)
    if status != "ok":
        raise BookRejected(status, REJECTED_MESSAGES[status].format(max_words=cfg.max_words))

    system = prompts.load_prompt(prompts.SYSTEM)

    # 2. Book check (once per book)
    if store.book_check:
        check = BookCheck.from_dict(store.book_check)
    else:
        on_progress(Progress("analyzing", message="Checking the book type and chapters"))
        check, replies = run_book_check(llm, book, cfg)
        for reply in replies:
            _record(store, "book check", reply)
        store.save_book_check(check.to_dict())

    # 3. Plan the chunks and save the plan before summarizing starts
    chunks = plan_chunks(book, cfg.chunk_target_words, check.chapters, check.skip_sections).chunks
    chunks = split_oversized(chunks, max_chunk_words(system, cfg))
    plan = [{"chapter_titles": c.chapter_titles, "word_count": c.word_count} for c in chunks]
    if store.plan != plan:
        if store.plan is not None:
            on_progress(Progress("summarizing", message=(
                "The chunks differ from the last run (were the settings changed?). "
                "Starting the chunk notes again."
            )))
        store.save_plan(plan)

    # 4. Notes for each chunk
    total = len(chunks)
    done = store.chunk_results
    if done:
        on_progress(Progress("summarizing", len(done), total,
                             f"Resuming: {len(done)} of {total} parts already done"))
    for chunk in chunks:
        if chunk.index in done:
            continue
        on_progress(Progress("summarizing", len(done), total,
                             f"Summarizing part {chunk.index + 1} of {total}"))
        previous = done.get(chunk.index - 1, {}).get("carryover")
        reply = llm.chat(cfg.llm_model_chunk, system,
                         chunk_prompt(book, check, chunk, total, previous, cfg))
        _record(store, f"chunk {chunk.index + 1}", reply)
        notes, carryover = split_carryover(reply.text)
        store.save_chunk_result(chunk.index, notes, carryover, reply.seconds)
        done[chunk.index] = {"notes": notes, "carryover": carryover}

    # 5. Merge notes if they don't fit in the final prompt
    blocks = [
        NoteBlock(c.index + 1, c.index + 1, c.chapter_titles, done[c.index]["notes"])
        for c in chunks
    ]
    blocks = merge_until_fits(blocks, llm, store, book, check, system, cfg, on_progress)

    # 6. Final summary
    on_progress(Progress("finalizing", message="Writing the final summary"))
    reply = llm.chat(cfg.llm_model_final, system, final_prompt(book, check, blocks), final=True)
    _record(store, "final summary", reply)
    store.save_summary(reply.text, reply.model)
    on_progress(Progress("done", total, total, "Summary ready"))
    return reply.text


def _record(store, step: str, reply: LLMReply) -> None:
    store.add_call(step, reply.model, reply.seconds, reply.prompt_tokens, reply.completion_tokens)


# ---------------------------------------------------------------- chunk notes


def chunk_prompt(
    book: ExtractedBook, check: BookCheck, chunk: Chunk, total: int,
    previous_carryover: str | None, cfg: Settings,
) -> str:
    if previous_carryover:
        carryover = cap_words(previous_carryover, CARRYOVER_MAX_WORDS)
    else:
        carryover = "None. This is the start of the book."
    return prompts.fill(prompts.load_prompt(prompts.CHUNK_SUMMARY), {
        "book_title": book.title,
        "book_type": check.book_type.upper(),
        "subject": check.subject,
        "chunk_number": chunk.index + 1,
        "total_chunks": total,
        "chapter_titles": ", ".join(chunk.chapter_titles) or "(not known)",
        "chunk_position_note": position_note(chunk.index, total),
        "previous_carryover": carryover,
        "chunk_text": chunk.text,
        "max_words": cfg.chunk_notes_max_words,
    })


def position_note(index: int, total: int) -> str:
    if total == 1:
        return "This chunk is the whole book."
    if index == 0:
        return "This is the beginning of the book."
    if index == total - 1:
        return "This is the end of the book. For fiction, make sure the ending is covered."
    return "This is a middle part of the book."


def max_chunk_words(system: str, cfg: Settings) -> int:
    """The most book text (in words) a chunk prompt can hold within the budget.

    Budget minus everything else in the prompt: the system prompt, the
    template, the longest carryover we pass on, and room for chapter titles.
    """
    empty = prompts.fill(prompts.load_prompt(prompts.CHUNK_SUMMARY), {
        "book_title": "x " * 20, "book_type": "LEARNING", "subject": "x " * 10,
        "chunk_number": 999, "total_chunks": 999,
        "chapter_titles": CHAPTER_TITLES_ALLOWANCE,
        "chunk_position_note": position_note(1, 3),
        "previous_carryover": "x " * CARRYOVER_MAX_WORDS,
        "chunk_text": "", "max_words": 999,
    })
    room = cfg.prompt_budget() - estimate_tokens(system) - estimate_tokens(empty)
    words = tokens_to_words(room)
    if words < MIN_CHUNK_WORDS:
        raise LLMError(
            f"LLM_CONTEXT_TOKENS={cfg.llm_context_tokens} is too small to hold a chunk "
            "of the book. Use at least 8192."
        )
    return words


_CARRYOVER_HEADING = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)carryover\b[^\n]*$", re.IGNORECASE | re.MULTILINE)
# A Markdown heading, or a line that is all bold ("**Key points**").
_ANY_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*\n]+\*\*:?\s*$)", re.MULTILINE)


def split_carryover(reply: str) -> tuple[str, str]:
    """Split a chunk reply into (notes, carryover).

    The carryover is the "## Carryover" section. It's only for the next
    chunk, so it's taken out of the notes used for the final summary.
    """
    match = _CARRYOVER_HEADING.search(reply)
    if not match:
        return reply.strip(), _fallback_carryover(reply)
    after = reply[match.end():]
    next_heading = _ANY_HEADING.search(after)
    carryover = after[: next_heading.start()] if next_heading else after
    rest = after[next_heading.start():] if next_heading else ""
    notes = (reply[: match.start()].rstrip() + "\n\n" + rest.strip()).strip()
    return notes, carryover.strip() or _fallback_carryover(notes)


def _fallback_carryover(notes: str) -> str:
    # No carryover section: pass on the start of the notes (the "Summary").
    lines = [line for line in notes.splitlines() if not line.lstrip().startswith("#")]
    return cap_words(" ".join(lines), FALLBACK_CARRYOVER_WORDS)


def cap_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]) + " ..."


# ---------------------------------------------------------------- merging


def final_notes_room(book: ExtractedBook, check: BookCheck, system: str, cfg: Settings) -> int:
    """Tokens available for the notes inside the final prompt."""
    empty = final_prompt(book, check, [])
    return cfg.prompt_budget(final=True) - estimate_tokens(system) - estimate_tokens(empty)


def merge_until_fits(
    blocks: list[NoteBlock], llm: LLMClient, store, book: ExtractedBook, check: BookCheck,
    system: str, cfg: Settings, on_progress: Callable[[Progress], None],
) -> list[NoteBlock]:
    room = final_notes_room(book, check, system, cfg)
    round_number = 0
    while estimate_tokens(render_blocks(blocks)) > room:
        round_number += 1
        batches = plan_merge_batches(blocks, room, system, cfg)
        merged = []
        for i, batch in enumerate(batches):
            if len(batch) == 1:  # nothing to combine; keep it as it is
                merged.append(batch[0])
                continue
            on_progress(Progress("merging", i, len(batches),
                                 f"Combining notes (round {round_number}): {i + 1} of {len(batches)}"))
            reply = llm.chat(cfg.llm_model_chunk, system, merge_prompt(book, check, batch, cfg))
            _record(store, f"merge {round_number}, parts {batch[0].first}-{batch[-1].last}", reply)
            titles = [t for b in batch for t in b.titles]
            merged.append(NoteBlock(batch[0].first, batch[-1].last, titles, reply.text))
        if len(merged) >= len(blocks):  # can't happen, but never loop forever
            raise LLMError("Merging the notes did not make them shorter.")
        blocks = merged
    return blocks


def plan_merge_batches(
    blocks: list[NoteBlock], room: int, system: str, cfg: Settings
) -> list[list[NoteBlock]]:
    """Group consecutive note blocks into batches to merge.

    We use as many batches as the final prompt can hold (each merged result
    is up to MERGE_NOTES_MAX_WORDS), so each merge combines only a few
    notes and keeps as much detail as possible. But each batch should hold
    at least two notes, so at most half as many batches as notes. Each
    batch must also fit in one merge prompt; if one doesn't, we use more,
    smaller batches.
    """
    merged_size = estimate_tokens("x " * (cfg.merge_notes_max_words + 20))  # + header
    count = max(1, min(len(blocks) // 2, room // merged_size))
    while count < len(blocks):
        batches = _group(blocks, count)
        if all(_merge_fits(batch, system, cfg) for batch in batches):
            return batches
        count += 1
    raise LLMError("The chunk notes are too long to merge. Try a lower CHUNK_NOTES_MAX_WORDS.")


def _group(blocks: list[NoteBlock], count: int) -> list[list[NoteBlock]]:
    # split_evenly balances by word count; map its groups of text back to blocks.
    groups = split_evenly([b.render() for b in blocks], count)
    out, i = [], 0
    for group in groups:
        out.append(blocks[i : i + len(group)])
        i += len(group)
    return out


def _merge_fits(batch: list[NoteBlock], system: str, cfg: Settings) -> bool:
    # A placeholder book/check: only the size of these values matters here.
    prompt = prompts.fill(prompts.load_prompt(prompts.MERGE_NOTES), {
        "book_title": "x " * 20, "book_type": "LEARNING",
        "first_part": batch[0].first, "last_part": batch[-1].last,
        "notes_batch": render_blocks(batch), "max_words": cfg.merge_notes_max_words,
    })
    return estimate_tokens(system) + estimate_tokens(prompt) <= cfg.prompt_budget()


def merge_prompt(book: ExtractedBook, check: BookCheck, batch: list[NoteBlock], cfg: Settings) -> str:
    return prompts.fill(prompts.load_prompt(prompts.MERGE_NOTES), {
        "book_title": book.title,
        "book_type": check.book_type.upper(),
        "first_part": batch[0].first,
        "last_part": batch[-1].last,
        "notes_batch": render_blocks(batch),
        "max_words": cfg.merge_notes_max_words,
    })


# ---------------------------------------------------------------- final summary


def final_prompt(book: ExtractedBook, check: BookCheck, blocks: list[NoteBlock]) -> str:
    return prompts.fill(prompts.final_prompt_template(check.book_type), {
        "book_title": book.title,
        "author": book.author,
        "book_type": check.book_type.upper(),
        "subject": check.subject,
        "page_count": book.page_estimate,
        "reader_level": check.reader_level,
        "all_chunk_notes": render_blocks(blocks),
    })


def _short_titles(titles: list[str]) -> str:
    """'Ch 2 (part 1 of 3)', 'Ch 2 (part 2 of 3)', 'Ch 3' -> 'Ch 2, Ch 3'."""
    unique = []
    for title in titles:
        title = re.sub(r" \(part \d+ of \d+\)$", "", title)
        if title not in unique:
            unique.append(title)
    if len(unique) > 4:
        return f"{unique[0]} ... {unique[-1]}"
    return ", ".join(unique)
