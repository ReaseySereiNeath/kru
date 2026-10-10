import pytest

from pipeline import prompts
from pipeline.prompts import PromptError, fill, final_prompt_template, load_prompt, placeholders


def test_fill_replaces_known_names():
    assert fill("Hi {name}, {name}!", {"name": "Kru"}) == "Hi Kru, Kru!"


def test_fill_leaves_json_braces_alone():
    template = 'Title: {title}\n{"number": 1, "title": "..."}'
    assert fill(template, {"title": "A"}) == 'Title: A\n{"number": 1, "title": "..."}'


def test_fill_does_not_touch_placeholders_inside_inserted_text():
    # A coding book may contain "{max_words}" in its text. It must stay as is.
    template = "<text>{chunk_text}</text> Under {max_words} words."
    out = fill(template, {"chunk_text": "print(f'{max_words}')", "max_words": 250})
    assert out == "<text>print(f'{max_words}')</text> Under 250 words."


def test_fill_complains_about_missing_values():
    with pytest.raises(PromptError, match="book_title"):
        fill("{book_title} {author}", {"author": "A"})


def test_extra_values_are_ignored():
    assert fill("{a}", {"a": 1, "b": 2}) == "1"


@pytest.mark.parametrize("name, expected", [
    (prompts.BOOK_CHECK, {"book_title", "author", "word_count", "table_of_contents", "first_pages_text"}),
    (prompts.SYSTEM, set()),
    (prompts.CHUNK_SUMMARY, {"book_title", "book_type", "subject", "chunk_number", "total_chunks",
                             "chapter_titles", "chunk_position_note", "previous_carryover",
                             "chunk_text", "max_words"}),
    (prompts.MERGE_NOTES, {"book_title", "book_type", "first_part", "last_part", "notes_batch", "max_words"}),
    (prompts.FINAL_SUMMARY, {"book_title", "author", "book_type", "subject", "page_count",
                             "reader_level", "all_chunk_notes"}),
])
def test_prompt_files_have_the_placeholders_the_code_fills(name, expected):
    # If someone edits a prompt file and renames a placeholder, this fails.
    assert placeholders(load_prompt(name)) == expected


def test_final_prompt_fiction_half():
    text = final_prompt_template("fiction")
    assert "## The full story" in text
    assert "## Key concepts" not in text
    assert "===" not in text
    assert "{all_chunk_notes}" in text  # the shared top part is kept


def test_final_prompt_learning_half():
    text = final_prompt_template("learning")
    assert "## Key concepts" in text
    assert "## The full story" not in text
    assert "===" not in text
    assert "LENGTH GUIDE" in text
