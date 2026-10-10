import json

import pytest

from pipeline.book_check import (
    BookCheckError,
    build_prompt,
    parse_json_reply,
    run_book_check,
    to_book_check,
)
from pipeline.extract import ExtractedBook, Section, extract_book
from pipeline.llm import LLMClient
from tests.conftest import FakeServer

GOOD = {
    "book_type": "fiction", "subject": "seaside novel", "reader_level": "beginner",
    "chapters": [{"number": 1, "title": "The Storm"}, {"number": 2, "title": "Morning"}],
    "skip_sections": ["Copyright"],
}


@pytest.mark.parametrize("reply", [
    json.dumps(GOOD),
    "```json\n" + json.dumps(GOOD) + "\n```",
    "```\n" + json.dumps(GOOD) + "\n```",
    "Here is the JSON you asked for:\n" + json.dumps(GOOD, indent=2) + "\nHope this helps!",
])
def test_parse_json_reply_handles_messy_replies(reply):
    assert parse_json_reply(reply) == GOOD


@pytest.mark.parametrize("reply", ["no json here", "{broken: json,}", "[1, 2]"])
def test_parse_json_reply_rejects_bad_replies(reply):
    with pytest.raises(ValueError):
        parse_json_reply(reply)


def test_to_book_check():
    check = to_book_check(GOOD)
    assert check.book_type == "fiction"
    assert check.chapters == ["The Storm", "Morning"]
    assert check.skip_sections == ["Copyright"]


def test_to_book_check_fixes_small_mistakes():
    check = to_book_check({
        "book_type": "Non-Fiction", "reader_level": "expert",
        "chapters": ["Plain string", {"title": "  "}, {"number": 3}, 42],
        "skip_sections": None,
    })
    assert check.book_type == "learning"
    assert check.reader_level == "beginner"
    assert check.subject == "general"
    assert check.chapters == ["Plain string"]
    assert check.skip_sections == []


def test_prompt_uses_toc_when_there_is_one(samples):
    book = extract_book(samples["epub"])
    prompt = build_prompt(book)
    assert "Chapter 2: The Long Night" in prompt.split("<opening_pages>")[0]
    assert "The Little Lighthouse" in prompt
    assert '"book_type": "fiction" or "learning"' in prompt  # the JSON example survives filling


def test_prompt_without_toc_sends_more_opening_text():
    book = ExtractedBook("T", "A", "txt", [Section("", ["word " * 5000])])
    prompt = build_prompt(book)
    assert "(no table of contents found)" in prompt
    opening = prompt.split("<opening_pages>")[1].split("</opening_pages>")[0]
    assert len(opening.split()) == 3000


def test_run_book_check_retries_once_on_bad_json(cfg, samples):
    replies = iter(["Sorry, I can't do JSON", json.dumps(GOOD)])
    server = FakeServer(lambda r: next(replies))
    check, calls = run_book_check(LLMClient(cfg, client=server), extract_book(samples["txt"]), cfg)
    assert check.book_type == "fiction"
    assert len(calls) == 2
    assert "not valid JSON" in server.user_messages()[1]
    assert server.requests[0]["model"] == "chunk-model"


def test_run_book_check_fails_after_two_bad_replies(cfg, samples):
    server = FakeServer(lambda r: "still not json")
    with pytest.raises(BookCheckError):
        run_book_check(LLMClient(cfg, client=server), extract_book(samples["txt"]), cfg)
    assert len(server.requests) == 2
