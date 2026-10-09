"""Show how Kru reads and splits a book. No AI involved.

Run from the backend folder:
    python -m pipeline.chunk_book path/to/book.pdf
    python -m pipeline.chunk_book path/to/book.epub --target-words 2500
    python -m pipeline.chunk_book path/to/book.txt --preview

It prints the title, author, word count, page estimate, whether the book
passes the limits, and the list of chunks the summary step would use.
"""

import argparse
import sys
import time
from pathlib import Path

from app.config import settings
from pipeline.chunking import plan_chunks
from pipeline.extract import ExtractionError, book_status, check_file_size, extract_book

STATUS_MESSAGES = {
    "too_long": "Too long: the limit is {max_words:,} words (about 500 pages).",
    "scanned_not_supported": (
        "This PDF looks scanned (pages are images, not text). "
        "Scanned books are not supported yet."
    ),
}

METHOD_NAMES = {
    "file structure": "the file's own chapters (PDF bookmarks / EPUB table of contents)",
    "chapter titles": "chapter titles found in the text",
    "paragraphs": "paragraphs (no chapters found)",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract and chunk a book (no AI).")
    parser.add_argument("path", type=Path, help="a .pdf, .epub, or .txt file")
    parser.add_argument(
        "--target-words", type=int, default=settings.chunk_target_words,
        help=f"words per chunk (default: CHUNK_TARGET_WORDS = {settings.chunk_target_words})",
    )
    parser.add_argument(
        "--preview", action="store_true", help="also print the start of each chunk"
    )
    args = parser.parse_args()

    if not args.path.is_file():
        print(f"Error: file not found: {args.path}")
        return 2

    started = time.perf_counter()
    try:
        check_file_size(args.path, settings.max_file_mb)
        book = extract_book(args.path)
    except ExtractionError as e:
        print(f"Error: {e}")
        return 2
    seconds = time.perf_counter() - started

    pages = f", {book.page_count} pages" if book.page_count else ""
    print(f"File:     {args.path.name}")
    print(f"Title:    {book.title}")
    print(f"Author:   {book.author}")
    print(f"Type:     {book.file_type.upper()}{pages}")
    print(f"Words:    {book.word_count:,} (about {book.page_estimate} pages at 400 words/page)")
    print(f"TOC:      {len(book.toc)} entries")
    print(f"Read in:  {seconds:.1f} s")

    status = book_status(book, settings.max_words)
    if status != "ok":
        print(f"Status:   {status}")
        print(STATUS_MESSAGES[status].format(max_words=settings.max_words))
        return 1
    print("Status:   ok")

    # In Phase 3 the book check will also pass chapter titles and
    # sections to skip. Here we only use what's in the file.
    plan = plan_chunks(book, args.target_words)
    print(f"Chapters: from {METHOD_NAMES[plan.method]}")
    print(f"Chunks:   {len(plan.chunks)} (target {args.target_words:,} words each)")
    print()
    print(f"{'#':>4}  {'words':>6}  chapters")
    for chunk in plan.chunks:
        titles = ", ".join(chunk.chapter_titles) or "-"
        print(f"{chunk.index + 1:>4}  {chunk.word_count:>6,}  {titles}")
        if args.preview:
            snippet = " ".join(chunk.text.split()[:40])
            print(f"{'':>14}{snippet} ...")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
