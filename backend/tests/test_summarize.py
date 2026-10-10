import json
import re
from dataclasses import replace

import pytest

from pipeline.chunking import Chunk, split_oversized
from pipeline.llm import LLMClient, LLMError, estimate_tokens
from pipeline.store import JsonStore
from pipeline.summarize import (
    BookRejected,
    NoteBlock,
    cap_words,
    plan_merge_batches,
    position_note,
    render_blocks,
    split_carryover,
    summarize_book,
)
from tests.conftest import FakeServer

BOOK_CHECK = {
    "book_type": "fiction", "subject": "seaside novel", "reader_level": "beginner",
    "chapters": [{"number": 1, "title": "Chapter 1: The Storm"}],
    "skip_sections": ["Copyright"],
}


class FakeModel:
    """Answers each kind of prompt the pipeline sends, like a (very dull) model."""

    def __init__(self, notes_words=20, merge_words=20, crash_on_chunk=None):
        self.notes_words = notes_words
        self.merge_words = merge_words
        self.crash_on_chunk = crash_on_chunk

    def __call__(self, request):
        user = request["messages"][1]["content"]
        if "response_format" in request:
            return json.dumps(BOOK_CHECK)
        if "Combine these notes" in user:
            return "Merged. " + "m " * self.merge_words
        if "create the final summary" in user:
            return "# Final summary"
        n = int(re.search(r"Chunk (\d+) of", user).group(1))
        if n == self.crash_on_chunk:
            raise KeyboardInterrupt  # like the computer stopping mid-book
        return (f"## Summary\nNotes for chunk {n}. " + "n " * self.notes_words
                + f"\n\n## Carryover\nCarry from chunk {n}.")


def run(path, cfg, store, model, progress=None):
    server = FakeServer(model)
    llm = LLMClient(cfg, client=server, sleep=lambda s: None)
    result = summarize_book(path, llm, store, cfg, on_progress=(progress if progress is not None else []).append)
    return result, server


def chunk_prompts(server):
    return [u for u in server.user_messages() if "<book_text>" in u]


# ---------------------------------------------------------------- pieces


def test_split_carryover():
    reply = "## Summary\nThings happen.\n\n## Carryover\nAlice is lost.\n\n## Extra\nMore."
    notes, carry = split_carryover(reply)
    assert carry == "Alice is lost."
    assert notes == "## Summary\nThings happen.\n\n## Extra\nMore."


def test_split_carryover_bold_heading_and_bold_names():
    reply = "**Summary**\nStuff.\n\n**Carryover:**\n**Alice** is lost in the woods."
    notes, carry = split_carryover(reply)
    assert carry == "**Alice** is lost in the woods."
    assert notes == "**Summary**\nStuff."


def test_split_carryover_missing_uses_start_of_notes():
    notes, carry = split_carryover("## Summary\nThe keeper lights the lamp.")
    assert notes == "## Summary\nThe keeper lights the lamp."
    assert carry == "The keeper lights the lamp."


def test_cap_words():
    assert cap_words("a b c", 5) == "a b c"
    assert cap_words("a b c d", 2) == "a b ..."


def test_position_note():
    assert "whole book" in position_note(0, 1)
    assert "beginning" in position_note(0, 5)
    assert "ending" in position_note(4, 5)
    assert "middle" in position_note(2, 5)


def test_render_blocks():
    blocks = [NoteBlock(1, 1, ["Ch 1"], "one"),
              NoteBlock(2, 4, ["Ch 2 (part 1 of 2)", "Ch 2 (part 2 of 2)", "Ch 3"], "two")]
    assert render_blocks(blocks) == "### Part 1 (Ch 1)\n\none\n\n### Parts 2-4 (Ch 2, Ch 3)\n\ntwo"


def test_merge_batches_fit_and_keep_order(cfg):
    blocks = [NoteBlock(i, i, [], "word " * 300) for i in range(1, 41)]
    room = 6000  # tokens for notes in the final prompt
    batches = plan_merge_batches(blocks, room, "system", cfg)
    # As many batches as the final prompt can hold: 6000 // (420 words x 1.4) = 10.
    assert len(batches) == 10
    assert [b for batch in batches for b in batch] == blocks  # nothing lost or reordered
    assert all(len(batch) >= 2 for batch in batches)


def test_merge_batches_get_smaller_when_too_big_for_one_prompt(cfg):
    small = replace(cfg, llm_context_tokens=6000)  # merge budget 3,500 tokens
    blocks = [NoteBlock(i, i, [], "word " * 1000) for i in range(1, 8)]
    # 7 notes -> 3 batches (2, 3, 2) would be the plan, but 3 x 1,000 words
    # is too big for one merge prompt, so it uses 4 batches of at most 2.
    batches = plan_merge_batches(blocks, room=100_000, system="", cfg=small)
    assert len(batches) == 4
    assert max(len(b) for b in batches) == 2


def test_merge_impossible_when_two_notes_never_fit(cfg):
    small = replace(cfg, llm_context_tokens=6000)
    blocks = [NoteBlock(i, i, [], "word " * 1500) for i in range(1, 5)]
    with pytest.raises(LLMError, match="too long to merge"):
        plan_merge_batches(blocks, room=100_000, system="", cfg=small)


def test_split_oversized_chunks():
    text = "\n\n".join(["## Ch 1"] + ["word " * 100] * 10)
    chunks = split_oversized([Chunk(0, ["Ch 1"], text, 1000), Chunk(1, ["Ch 2"], "small", 1)], 400)
    assert [c.index for c in chunks] == [0, 1, 2, 3]
    assert all(c.word_count <= 400 for c in chunks)
    assert sum(c.word_count for c in chunks[:3]) == 1000
    assert chunks[3].text == "small"


# ---------------------------------------------------------------- whole pipeline


def test_full_run(cfg, samples, tmp_path):
    cfg = replace(cfg, chunk_target_words=500)
    store = JsonStore(tmp_path / "run.json")
    progress = []
    summary, server = run(samples["epub"], cfg, store, FakeModel(), progress)

    assert summary == "# Final summary"
    prompts = chunk_prompts(server)
    assert len(prompts) == len(store.plan) == 4
    # Carryover goes from each chunk to the next one.
    assert "None. This is the start of the book." in prompts[0]
    assert "Carry from chunk 1." in prompts[1]
    assert "Carry from chunk 3." in prompts[3]
    # The book check's skip_sections removed the copyright page.
    assert "All rights reserved" not in "".join(prompts)
    # Chunk notes use the chunk model, the final summary the final model.
    models = [r["model"] for r in server.requests]
    assert models == ["chunk-model"] * 5 + ["final-model"]
    # The final prompt: fiction half only, notes without carryover sections.
    final = server.user_messages()[-1]
    assert "## The full story" in final and "## Key concepts" not in final
    assert "### Part 1 (Chapter 1: The Storm)" in final
    assert "Notes for chunk 4." in final and "Carry from" not in final
    # Progress went through every step, ending at done.
    assert [p.status for p in progress][0] == "extracting"
    assert "Summarizing part 4 of 4" in [p.message for p in progress]
    assert progress[-1].status == "done"
    # Every call was recorded (book check + 4 chunks + final).
    assert len(store.calls) == 6
    assert store.summary == {"markdown": "# Final summary", "model": "final-model"}


def test_resume_after_crash(cfg, samples, tmp_path):
    cfg = replace(cfg, chunk_target_words=500)
    path = tmp_path / "run.json"
    with pytest.raises(KeyboardInterrupt):
        run(samples["epub"], cfg, JsonStore(path), FakeModel(crash_on_chunk=3))

    # A new process: progress is loaded from the file.
    store = JsonStore(path)
    assert sorted(store.chunk_results) == [0, 1]
    progress = []
    summary, server = run(samples["epub"], cfg, store, FakeModel(), progress)

    assert summary == "# Final summary"
    prompts = chunk_prompts(server)
    # No new book check; only chunks 3 and 4 are summarized again.
    assert not any("response_format" in r for r in server.requests)
    assert [re.search(r"Chunk (\d+) of", p).group(1) for p in prompts] == ["3", "4"]
    # Chunk 3 still gets chunk 2's carryover, saved before the crash.
    assert "Carry from chunk 2." in prompts[0]
    assert "Resuming: 2 of 4 parts already done" in [p.message for p in progress]
    # The final summary still has the notes from before the crash.
    assert "Notes for chunk 1." in server.user_messages()[-1]


def test_finished_book_is_not_summarized_again(cfg, samples, tmp_path):
    store = JsonStore(tmp_path / "run.json")
    run(samples["txt"], cfg, store, FakeModel())
    summary, server = run(samples["txt"], cfg, JsonStore(tmp_path / "run.json"), FakeModel())
    assert summary == "# Final summary"
    assert server.requests == []


def test_changed_settings_restart_chunk_notes(cfg, samples, tmp_path):
    path = tmp_path / "run.json"
    with pytest.raises(KeyboardInterrupt):
        run(samples["epub"], replace(cfg, chunk_target_words=500), JsonStore(path),
            FakeModel(crash_on_chunk=3))
    # Same book, different chunk size: old notes don't match the new chunks.
    progress = []
    _, server = run(samples["epub"], replace(cfg, chunk_target_words=300), JsonStore(path),
                    FakeModel(), progress)
    assert len(chunk_prompts(server)) == len(JsonStore(path).plan) > 4
    assert any("differ from the last run" in p.message for p in progress)


def test_notes_are_merged_when_too_long_for_final_prompt(cfg, samples, tmp_path):
    # A small context and long notes force the merge step, over several rounds.
    cfg = replace(cfg, llm_context_tokens=9000, chunk_target_words=300)
    store = JsonStore(tmp_path / "run.json")
    progress = []
    summary, server = run(samples["epub"], cfg, store, FakeModel(notes_words=600, merge_words=600),
                          progress)

    assert summary == "# Final summary"
    merges = [u for u in server.user_messages() if "Combine these notes" in u]
    assert len(merges) >= 2
    # Every merge combines at least two parts (none is "parts 3 to 3").
    for m in merges:
        first, last = re.search(r"Notes for parts (\d+) to (\d+)", m).groups()
        assert int(last) > int(first)
    assert any(p.status == "merging" for p in progress)
    final = server.user_messages()[-1]
    assert "### Parts " in final
    # The final prompt fits its budget (the client would have refused it otherwise).
    assert estimate_tokens(final) <= cfg.prompt_budget(final=True)


def test_too_long_book_is_rejected_before_any_model_call(cfg, samples, tmp_path):
    store = JsonStore(tmp_path / "run.json")
    with pytest.raises(BookRejected) as info:
        run(samples["pdf"], replace(cfg, max_words=1000), store, FakeModel())
    assert info.value.status == "too_long"
    assert "1,000 words" in str(info.value)
    assert store.calls == []
