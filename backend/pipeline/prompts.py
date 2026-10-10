"""Load the prompt templates in /prompts and fill in their {placeholders}.

Why not Python's str.format()? The prompts contain literal JSON braces like
{"number": 1, ...}, which str.format() would try (and fail) to fill. So we
only replace names we know, like {book_title}.

We also fill every placeholder in ONE pass over the template. If we replaced
them one by one, a book about programming that contains the text "{max_words}"
inside {chunk_text} would get that text replaced too.
"""

import re

from app.config import PROMPTS_DIR

BOOK_CHECK = "0_book_check.txt"
SYSTEM = "1_system.txt"
CHUNK_SUMMARY = "2_chunk_summary.txt"
MERGE_NOTES = "2b_merge_notes.txt"
FINAL_SUMMARY = "3_final_summary.txt"

# A placeholder is a lowercase name in braces: {book_title}. JSON like
# {"number": 1} never matches because of the quotes.
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

FICTION_MARKER = "=== IF FICTION ==="
LEARNING_MARKER = "=== IF LEARNING ==="


class PromptError(Exception):
    pass


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template))


def fill(template: str, values: dict[str, object]) -> str:
    """Replace each {name} in the template with str(values[name])."""
    missing = placeholders(template) - values.keys()
    if missing:
        raise PromptError(f"No value given for: {', '.join(sorted(missing))}")
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


def final_prompt_template(book_type: str) -> str:
    """The final summary prompt with only the FICTION or the LEARNING half.

    The file has a shared top part, then "=== IF FICTION ===" and
    "=== IF LEARNING ===" sections. Sending both would confuse a small model
    and waste context, so we cut out the one that applies.
    """
    text = load_prompt(FINAL_SUMMARY)
    if FICTION_MARKER not in text or LEARNING_MARKER not in text:
        raise PromptError(f"{FINAL_SUMMARY} must contain both '{FICTION_MARKER}' and '{LEARNING_MARKER}'")
    shared, rest = text.split(FICTION_MARKER, 1)
    fiction, learning = rest.split(LEARNING_MARKER, 1)
    chosen = fiction if book_type == "fiction" else learning
    return shared.rstrip() + "\n\n" + chosen.strip() + "\n"
