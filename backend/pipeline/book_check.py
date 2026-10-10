"""Book check: ask the model what kind of book this is and list its chapters.

Uses prompts/0_book_check.txt. The model must reply with JSON. Small models
often wrap JSON in ``` fences or add a sentence before it, so we clean the
reply before parsing, and ask once more if it still isn't valid JSON.
"""

import json
import re
from dataclasses import asdict, dataclass, field

from app.config import Settings
from pipeline import prompts
from pipeline.extract import ExtractedBook
from pipeline.llm import LLMClient, LLMError, LLMReply

# Without a table of contents, the model guesses from the opening pages.
OPENING_WORDS_NO_TOC = 3000
# With a table of contents, a shorter opening is enough to tell the book type.
OPENING_WORDS_WITH_TOC = 1000
MAX_TOC_LINES = 300

SYSTEM = "You analyze books. You reply with valid JSON only."
RETRY_NOTE = (
    "\n\nYour previous reply was not valid JSON. "
    "Reply again with ONLY the JSON object: no other text, no ``` fences."
)


class BookCheckError(LLMError):
    pass


@dataclass
class BookCheck:
    book_type: str  # "fiction" or "learning"
    subject: str
    reader_level: str  # "beginner", "intermediate", or "advanced"
    chapters: list[str] = field(default_factory=list)  # chapter titles
    skip_sections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BookCheck":
        return cls(**data)


def parse_json_reply(text: str) -> dict:
    """Get a JSON object out of a model reply. Raises ValueError if there isn't one."""
    text = text.strip()
    # Remove ``` or ```json fences.
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Ignore any words before the first { or after the last }.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the JSON is not an object")
    return data


def to_book_check(data: dict) -> BookCheck:
    """Turn the model's JSON into a BookCheck, fixing small mistakes."""
    book_type = str(data.get("book_type", "")).strip().lower()
    # e.g. "fiction" -> fiction, "non-fiction" or anything else -> learning
    book_type = "fiction" if book_type == "fiction" else "learning"

    reader_level = str(data.get("reader_level", "")).strip().lower()
    if reader_level not in ("beginner", "intermediate", "advanced"):
        reader_level = "beginner"

    chapters = []
    for chapter in data.get("chapters") or []:
        # Expected {"number": 1, "title": "..."}, but accept plain strings too.
        title = chapter.get("title") if isinstance(chapter, dict) else chapter
        if isinstance(title, str) and title.strip():
            chapters.append(title.strip())

    skip = [s.strip() for s in data.get("skip_sections") or [] if isinstance(s, str) and s.strip()]

    return BookCheck(
        book_type=book_type,
        subject=str(data.get("subject") or "").strip() or "general",
        reader_level=reader_level,
        chapters=chapters,
        skip_sections=skip,
    )


def build_prompt(book: ExtractedBook) -> str:
    if book.toc:
        toc = "\n".join(book.toc[:MAX_TOC_LINES])
        opening = book.first_words(OPENING_WORDS_WITH_TOC)
    else:
        toc = "(no table of contents found)"
        opening = book.first_words(OPENING_WORDS_NO_TOC)
    return prompts.fill(
        prompts.load_prompt(prompts.BOOK_CHECK),
        {
            "book_title": book.title,
            "author": book.author,
            "word_count": f"{book.word_count:,}",
            "table_of_contents": toc,
            "first_pages_text": opening,
        },
    )


def run_book_check(
    llm: LLMClient, book: ExtractedBook, cfg: Settings
) -> tuple[BookCheck, list[LLMReply]]:
    """Returns the result and the model replies (for time/token totals)."""
    prompt = build_prompt(book)
    replies = []
    for attempt in range(2):  # the first try, then once more if the JSON was bad
        reply = llm.chat(
            cfg.llm_model_chunk, SYSTEM, prompt + (RETRY_NOTE if attempt else ""), json_mode=True
        )
        replies.append(reply)
        try:
            return to_book_check(parse_json_reply(reply.text)), replies
        except ValueError:  # json.JSONDecodeError is a ValueError
            continue
    raise BookCheckError("The AI model did not return a valid book check (JSON) after two tries.")
