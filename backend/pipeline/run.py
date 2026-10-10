"""Summarize a book from the command line.

Run from the backend folder:
    python -m pipeline.run path/to/book.pdf
    python -m pipeline.run path/to/book.epub --restart     # ignore saved progress

Prints progress while it works, then the final summary, the time per chunk,
and the total time and tokens. The summary is also saved next to the
progress file in backend/runs/.

If it stops (Ctrl+C, crash, restart), run the same command again: it
continues from the last finished chunk.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from app.config import settings
from pipeline.extract import ExtractionError
from pipeline.llm import LLMClient, LLMError
from pipeline.store import JsonStore
from pipeline.summarize import BookRejected, Progress, summarize_book


def show_progress(p: Progress) -> None:
    print(f"[{p.status}] {p.message}", flush=True)


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a book with the local AI model.")
    parser.add_argument("path", type=Path, help="a .pdf, .epub, or .txt file")
    parser.add_argument("--restart", action="store_true",
                        help="forget saved progress for this book and start again")
    args = parser.parse_args()

    if not args.path.is_file():
        print(f"Error: file not found: {args.path}")
        return 2

    # Show one line per model call (time and tokens), indented under the progress lines.
    logging.basicConfig(level=logging.INFO, format="           %(message)s")
    # The HTTP library logs every request too; that's just noise here.
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    store = JsonStore.for_book(args.path)
    if args.restart:
        store.reset()
    print(f"Book:     {args.path.name}")
    print(f"Models:   {settings.llm_model_chunk} (chunks), {settings.llm_model_final} (final)")
    print(f"Progress: {store.path}")
    print()

    started = time.perf_counter()
    try:
        summary = summarize_book(args.path, LLMClient(), store, on_progress=show_progress)
    except BookRejected as e:
        print(f"\nNot summarized ({e.status}): {e}")
        return 1
    except (ExtractionError, LLMError) as e:
        print(f"\nError: {e}")
        print("Progress so far is saved. Fix the problem and run the same command again.")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to continue.")
        return 130
    this_run = time.perf_counter() - started

    out_path = store.path.with_name(f"{args.path.stem}.summary.md")
    out_path.write_text(summary + "\n", encoding="utf-8")

    print()
    print("=" * 72)
    print(summary)
    print("=" * 72)
    print()

    chunks = store.chunk_results
    if chunks:
        print("Time per chunk:")
        for index in sorted(chunks):
            titles = ", ".join(store.plan[index]["chapter_titles"]) or "-"
            words = store.plan[index]["word_count"]
            print(f"  {index + 1:>4}  {format_seconds(chunks[index]['seconds']):>10}"
                  f"  {words:>6,} words  {titles}")
        average = sum(c["seconds"] for c in chunks.values()) / len(chunks)
        print(f"  Average: {format_seconds(average)} per chunk")
        print()

    calls = store.calls
    tokens_in = sum(c["prompt_tokens"] for c in calls)
    tokens_out = sum(c["completion_tokens"] for c in calls)
    model_seconds = sum(c["seconds"] for c in calls)
    print(f"Model calls: {len(calls)}  ({tokens_in:,} tokens in, {tokens_out:,} out)")
    print(f"Model time:  {format_seconds(model_seconds)} in total, across all runs")
    print(f"This run:    {format_seconds(this_run)}")
    print(f"Saved to:    {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
