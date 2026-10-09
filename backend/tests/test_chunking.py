import random

import pytest

from pipeline.chunking import (
    OVERFLOW,
    is_heading_for,
    make_chunks,
    normalize,
    plan_chunks,
    remove_skipped,
    split_by_titles,
    split_evenly,
    split_long_paragraphs,
)
from pipeline.extract import Section, count_words, extract_book


def words(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def total_words(chunks) -> int:
    return sum(c.word_count for c in chunks)


# ---------------------------------------------------------------- matching titles


def test_normalize():
    assert normalize("  Chapter 1: The STORM! ") == "chapter 1 the storm"


@pytest.mark.parametrize("paragraph, title", [
    ("Chapter 1: The Storm", "Chapter 1: The Storm"),
    ("CHAPTER 1 — THE STORM", "Chapter 1: The Storm"),
    ("Chapter 1: The Storm", "The Storm"),        # heading has a "Chapter 1" prefix
    ("Chapter 1", "Chapter 1: The Storm"),        # heading is only the number
    ("The Storm", "Chapter 1: The Storm"),        # heading is only the name
])
def test_is_heading_for_matches(paragraph, title):
    assert is_heading_for(paragraph, title)


@pytest.mark.parametrize("paragraph, title", [
    ("The storm raged all night.", "The Storm"),  # a sentence, not a heading
    ("Storm", "Chapter 1: The Storm"),            # one word is too little
    ("Chapter 1", "Chapter 10: Later"),           # 1 is not 10
    (words(20), words(20)),                       # too long to be a heading
])
def test_is_heading_for_rejects(paragraph, title):
    assert not is_heading_for(paragraph, title)


def test_split_by_titles_ignores_printed_table_of_contents():
    titles = ["Chapter 1: Start", "Chapter 2: Middle", "Chapter 3: End"]
    paragraphs = (
        ["Contents"] + titles               # the printed TOC: titles next to each other
        + ["Chapter 1: Start", words(100, "a")]
        + ["Chapter 2: Middle", words(100, "b")]
        + ["Chapter 3: End", words(100, "c")]
    )
    sections = split_by_titles(paragraphs, titles)
    assert [s.title for s in sections] == [""] + titles
    assert sections[0].paragraphs == ["Contents"] + titles  # front matter
    assert sections[2].paragraphs == ["Chapter 2: Middle", words(100, "b")]


def test_split_by_titles_heading_directly_followed_by_another():
    titles = ["Part One", "Chapter 1: Start", "Chapter 2: End"]
    paragraphs = ["Part One", "Chapter 1: Start", words(100, "a"),
                  "Chapter 2: End", words(100, "b")]
    sections = split_by_titles(paragraphs, titles)
    # The empty "Part One" is dropped as a listing; the chapter it introduces is kept.
    assert [s.title for s in sections] == ["", "Chapter 1: Start", "Chapter 2: End"]


def test_split_by_titles_missing_title_stays_in_previous_chapter():
    titles = ["One", "Two", "Three"]
    paragraphs = ["One", words(50, "a"), words(50, "b"), "Three", words(50, "c")]
    sections = split_by_titles(paragraphs, titles)
    assert [s.title for s in sections] == ["One", "Three"]
    assert sections[0].word_count == 101


def test_split_by_titles_needs_two_matches():
    assert split_by_titles(["One", words(50)], ["One", "Two"]) is None
    assert split_by_titles(["One", words(50)], ["One"]) is None


def test_remove_skipped():
    sections = [Section("Copyright", ["c"]), Section("One", ["x"]),
                Section("INDEX.", ["i"]), Section("", ["untitled"])]
    kept = remove_skipped(sections, ["copyright", "Index"])
    assert [s.title for s in kept] == ["One", ""]


# ---------------------------------------------------------------- building chunks


def test_short_chapters_are_combined():
    sections = [Section(f"Ch {i}", [words(300)]) for i in range(4)]
    chunks = make_chunks(sections, target_words=1000)
    # 300 + 300 + 300 + 300 = 1200, which is exactly the limit (1000 x 1.2).
    assert len(chunks) == 1
    assert chunks[0].chapter_titles == ["Ch 0", "Ch 1", "Ch 2", "Ch 3"]


def test_chunk_starts_new_when_over_limit():
    sections = [Section(f"Ch {i}", [words(500)]) for i in range(3)]
    chunks = make_chunks(sections, target_words=1000)
    assert [c.chapter_titles for c in chunks] == [["Ch 0", "Ch 1"], ["Ch 2"]]
    assert [c.index for c in chunks] == [0, 1]


def test_long_chapter_is_split_into_equal_parts():
    section = Section("Big", [words(100, f"p{i}_") for i in range(25)])  # 2,500 words
    chunks = make_chunks([section], target_words=1000)
    assert [c.chapter_titles for c in chunks] == [
        ["Big (part 1 of 3)"], ["Big (part 2 of 3)"], ["Big (part 3 of 3)"]
    ]
    assert [c.word_count for c in chunks] == [800, 900, 800]


def test_chunk_text_marks_chapter_starts_without_repeating_headings():
    sections = [
        Section("One", ["One", "Text of one."]),  # text already starts with its heading
        Section("Two", ["Text of two."]),         # no heading in the text
    ]
    [chunk] = make_chunks(sections, target_words=1000)
    assert chunk.text == "One\n\nText of one.\n\n## Two\n\nText of two."
    assert chunk.word_count == 1 + 3 + 3  # the added "## Two" line is not counted


def test_split_evenly():
    parts = split_evenly([words(10)] * 9, 3)
    assert [len(p) for p in parts] == [3, 3, 3]


def test_split_long_paragraphs_at_sentence_ends():
    sentence = words(9) + "."  # 9 words
    para = " ".join([sentence] * 5)  # 45 words
    pieces = split_long_paragraphs([para, "short"], max_words=20)
    assert pieces[-1] == "short"
    assert all(count_words(p) <= 20 for p in pieces)
    assert all(p.endswith(".") for p in pieces[:-1])
    assert sum(count_words(p) for p in pieces) == 46


def test_split_long_paragraphs_with_no_sentence_ends():
    pieces = split_long_paragraphs([words(55)], max_words=20)
    assert [count_words(p) for p in pieces] == [20, 20, 15]


@pytest.mark.parametrize("target", [2000, 3000, 4000, 5000])
def test_chunks_keep_every_word_in_order_and_stay_near_target(target):
    # A made-up book: 40 chapters from 200 to 12,000 words.
    rng = random.Random(target)
    sections = []
    for c in range(40):
        paragraphs = [words(rng.randint(20, 400), f"c{c}p{p}_") for p in range(rng.randint(1, 60))]
        sections.append(Section(f"Chapter {c}", paragraphs))
    chunks = make_chunks(sections, target)

    all_words = [w for s in sections for p in s.paragraphs for w in p.split()]
    chunk_words = [w for c in chunks for w in c.text.split() if not w.startswith(("##", "Chapter", "("))]
    chunk_words = [w for w in chunk_words if not w.rstrip(")").isdigit() and w not in ("part", "of")]
    assert chunk_words == all_words
    assert total_words(chunks) == len(all_words)
    # Each chunk is at most a little over the limit (half a paragraph,
    # since long paragraphs are cut to half the target).
    assert max(c.word_count for c in chunks) <= target * OVERFLOW + target / 4


# ---------------------------------------------------------------- whole books


def test_plan_pdf_uses_file_structure(samples):
    plan = plan_chunks(extract_book(samples["pdf"]), target_words=500)
    assert plan.method == "file structure"
    assert plan.chunks[0].chapter_titles == ["Chapter 1: The Storm"]
    assert plan.chunks[-1].chapter_titles[-1] == "Chapter 3: Morning"


def test_plan_epub_skips_sections(samples):
    book = extract_book(samples["epub"])
    plan = plan_chunks(book, target_words=500, skip_sections=["Copyright"])
    assert "Copyright" not in plan.chunks[0].chapter_titles
    assert "All rights reserved" not in plan.chunks[0].text


def test_plan_txt_uses_chapter_titles_from_book_check(samples):
    book = extract_book(samples["txt"])
    titles = ["Chapter 1: The Storm", "Chapter 2: The Long Night", "Chapter 3: Morning"]
    plan = plan_chunks(book, target_words=500, chapter_titles=titles)
    assert plan.method == "chapter titles"
    assert plan.chunks[0].chapter_titles == ["Chapter 1: The Storm"]


def test_plan_txt_falls_back_to_paragraphs(samples):
    plan = plan_chunks(extract_book(samples["txt"]), target_words=500)
    assert plan.method == "paragraphs"
    assert all(c.chapter_titles == [] for c in plan.chunks)
    assert total_words(plan.chunks) == extract_book(samples["txt"]).word_count
