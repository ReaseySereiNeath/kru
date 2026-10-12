"""An in-memory stand-in for app.db.Database, with the same methods.

It lets the API and worker tests run without Supabase. The real SQL (Row
Level Security, claim_next_job) is checked separately against a real local
Supabase: see tests/test_supabase_live.py.
"""

import copy
import itertools

from app.db import ACTIVE_STATUSES


class FakeDatabase:
    def __init__(self):
        self.books: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}        # by job id
        self.chunks: dict[str, list[dict]] = {}  # by book id
        self.summaries: dict[str, dict] = {}   # by book id
        self.files: dict[str, bytes] = {}      # by storage path
        self.tokens: dict[str, str] = {}       # login token -> user id
        self._clock = itertools.count(1)

    def _now(self) -> str:
        # A fake clock that moves one second per call, so ordering is predictable.
        n = next(self._clock)
        return f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}+00:00"

    # --- auth
    def user_id_for_token(self, token):
        return self.tokens.get(token)

    # --- books
    def insert_book(self, fields):
        book = {
            "author": "Unknown", "file_path": None, "word_count": None, "page_estimate": None,
            "book_type": None, "subject": None, "reader_level": None, "book_check": None,
            "status": "queued", "total_tokens": 0, "total_seconds": 0.0,
            "created_at": self._now(), **fields,
        }
        self.books[book["id"]] = book
        return copy.deepcopy(book)

    def get_book(self, book_id):
        return copy.deepcopy(self.books.get(book_id))

    def list_books(self, user_id):
        books = [b for b in self.books.values() if b["user_id"] == user_id]
        return copy.deepcopy(sorted(books, key=lambda b: b["created_at"], reverse=True))

    def update_book(self, book_id, fields):
        if book_id in self.books:
            self.books[book_id].update(copy.deepcopy(fields))

    def delete_book(self, book_id):
        self.books.pop(book_id, None)
        self.chunks.pop(book_id, None)
        self.summaries.pop(book_id, None)
        for job_id in [j for j, job in self.jobs.items() if job["book_id"] == book_id]:
            del self.jobs[job_id]

    # --- jobs
    def insert_job(self, book_id):
        now = self._now()
        job = {
            "id": f"job-{len(self.jobs) + 1}", "book_id": book_id, "status": "queued",
            "progress_current": 0, "progress_total": 0, "progress_message": "", "error": None,
            "attempts": 0, "queued_at": now, "created_at": now, "updated_at": now,
        }
        self.jobs[job["id"]] = job
        return copy.deepcopy(job)

    def get_job(self, book_id):
        for job in self.jobs.values():
            if job["book_id"] == book_id:
                return copy.deepcopy(job)
        return None

    def update_job(self, job_id, fields):
        if job_id in self.jobs:
            self.jobs[job_id].update(fields)

    def claim_next_job(self):
        queued = sorted((j for j in self.jobs.values() if j["status"] == "queued"),
                        key=lambda j: j["queued_at"])
        if not queued:
            return None
        job = queued[0]
        job.update(status="extracting", attempts=job["attempts"] + 1, error=None,
                   progress_message="Starting")
        return copy.deepcopy(job)

    def requeue_stuck_jobs(self):
        stuck = [j for j in self.jobs.values() if j["status"] in ACTIVE_STATUSES]
        for job in stuck:
            job.update(status="queued", progress_message="Waiting to continue")
            self.update_book(job["book_id"], {"status": "queued"})
        return len(stuck)

    def queue_position(self, job):
        return 1 + sum(1 for j in self.jobs.values()
                       if j["status"] == "queued" and j["queued_at"] < job["queued_at"])

    # --- chunks
    def get_chunks(self, book_id):
        return copy.deepcopy(self.chunks.get(book_id, []))

    def replace_chunks(self, book_id, rows):
        self.chunks[book_id] = [
            {"notes": None, "carryover": None, "seconds_taken": None, "status": "pending",
             **copy.deepcopy(row), "book_id": book_id}
            for row in rows
        ]

    def update_chunk(self, book_id, chunk_index, fields):
        for chunk in self.chunks[book_id]:
            if chunk["chunk_index"] == chunk_index:
                chunk.update(fields)

    # --- summaries
    def get_summary(self, book_id):
        return copy.deepcopy(self.summaries.get(book_id))

    def save_summary(self, book_id, markdown, model):
        self.summaries[book_id] = {"book_id": book_id, "markdown": markdown, "model": model,
                                   "created_at": self._now()}

    def delete_summary(self, book_id):
        self.summaries.pop(book_id, None)

    # --- file storage
    def upload_file(self, path, data, content_type):
        self.files[path] = data

    def download_file(self, path):
        return self.files[path]

    def delete_file(self, path):
        self.files.pop(path, None)

    # --- health
    def ping(self):
        pass
