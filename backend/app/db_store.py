"""Pipeline progress saved in the database.

This is the database twin of pipeline/store.py's JsonStore: the same
methods, so summarize_book() works with either. Where JsonStore keeps one
JSON file per book, this keeps:
  book check + totals  -> the books row
  chunk plan + notes   -> chunks rows ('pending' until their notes are saved)
  final summary        -> the summaries row
"""

from app.db import Database


class DbStore:
    def __init__(self, db: Database, book_id: str):
        self.db = db
        self.book_id = book_id

    # --- facts read from the file (title, author, size)
    def save_book_info(self, title: str, author: str, word_count: int, page_estimate: int) -> None:
        self.db.update_book(self.book_id, {
            "title": title, "author": author,
            "word_count": word_count, "page_estimate": page_estimate,
        })

    # --- book check (runs once per book)
    @property
    def book_check(self) -> dict | None:
        return self.db.get_book(self.book_id)["book_check"]

    def save_book_check(self, check: dict) -> None:
        self.db.update_book(self.book_id, {
            "book_check": check,
            "book_type": check["book_type"],
            "subject": check["subject"],
            "reader_level": check["reader_level"],
        })

    # --- chunk plan
    @property
    def plan(self) -> list[dict] | None:
        rows = self.db.get_chunks(self.book_id)
        if not rows:
            return None
        return [{"chapter_titles": r["chapter_titles"], "word_count": r["word_count"]} for r in rows]

    def save_plan(self, plan: list[dict]) -> None:
        """Save a new plan as 'pending' chunks. Old notes and summary no longer fit, so they go."""
        self.db.replace_chunks(self.book_id, [
            {"chunk_index": i, "chapter_titles": c["chapter_titles"],
             "word_count": c["word_count"], "status": "pending"}
            for i, c in enumerate(plan)
        ])
        self.db.delete_summary(self.book_id)

    # --- finished chunks
    @property
    def chunk_results(self) -> dict[int, dict]:
        return {
            r["chunk_index"]: {"notes": r["notes"], "carryover": r["carryover"],
                               "seconds": r["seconds_taken"]}
            for r in self.db.get_chunks(self.book_id) if r["status"] == "done"
        }

    def save_chunk_result(self, index: int, notes: str, carryover: str, seconds: float) -> None:
        self.db.update_chunk(self.book_id, index, {
            "notes": notes, "carryover": carryover,
            "seconds_taken": round(seconds, 2), "status": "done",
        })

    # --- time and token totals for the book
    def add_call(self, step: str, model: str, seconds: float, prompt_tokens: int,
                 completion_tokens: int) -> None:
        # Read, add, write. Safe because only one worker handles a book at a time.
        book = self.db.get_book(self.book_id)
        self.db.update_book(self.book_id, {
            "total_tokens": book["total_tokens"] + prompt_tokens + completion_tokens,
            "total_seconds": round(book["total_seconds"] + seconds, 2),
        })

    # --- the final summary
    @property
    def summary(self) -> dict | None:
        row = self.db.get_summary(self.book_id)
        return {"markdown": row["markdown"], "model": row["model"]} if row else None

    def save_summary(self, markdown: str, model: str) -> None:
        self.db.save_summary(self.book_id, markdown, model)
