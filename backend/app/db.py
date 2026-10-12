"""All database and file-storage access, in one place.

The API and the worker call the small methods of `Database`; they never
build queries themselves. That keeps them easy to read, and lets the tests
replace the database with an in-memory fake (tests/fakes.py) that has the
same methods.

The backend connects with the SERVICE ROLE key, which skips Row Level
Security. So every method that returns a user's data must be given, and
must check, the user id. Never send this key to the browser.
"""

from datetime import datetime, timezone

from supabase import Client, create_client

from app.config import Settings, settings

BUCKET = "books"
# Statuses that mean "a worker is busy with this job right now".
ACTIVE_STATUSES = ["extracting", "analyzing", "summarizing", "merging", "finalizing"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, cfg: Settings = settings):
        if not cfg.supabase_url or not cfg.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are not set. "
                "Run `supabase status` and put the values in backend/.env (see README)."
            )
        self.client: Client = create_client(cfg.supabase_url, cfg.supabase_service_role_key)

    # ------------------------------------------------------------ auth

    def user_id_for_token(self, token: str) -> str | None:
        """Ask Supabase who this login token belongs to. None if it's not valid."""
        try:
            response = self.client.auth.get_user(token)
        except Exception:  # expired, malformed, or made-up token
            return None
        return response.user.id if response and response.user else None

    # ------------------------------------------------------------ books

    def insert_book(self, fields: dict) -> dict:
        return self.client.table("books").insert(fields).execute().data[0]

    def get_book(self, book_id: str) -> dict | None:
        rows = self.client.table("books").select("*").eq("id", book_id).execute().data
        return rows[0] if rows else None

    def list_books(self, user_id: str) -> list[dict]:
        return (
            self.client.table("books").select("*")
            .eq("user_id", user_id).order("created_at", desc=True).execute().data
        )

    def update_book(self, book_id: str, fields: dict) -> None:
        self.client.table("books").update(fields).eq("id", book_id).execute()

    def delete_book(self, book_id: str) -> None:
        # Jobs, chunks, and the summary go too ("on delete cascade" in the schema).
        self.client.table("books").delete().eq("id", book_id).execute()

    # ------------------------------------------------------------ jobs

    def insert_job(self, book_id: str) -> dict:
        return self.client.table("jobs").insert({"book_id": book_id}).execute().data[0]

    def get_job(self, book_id: str) -> dict | None:
        rows = self.client.table("jobs").select("*").eq("book_id", book_id).execute().data
        return rows[0] if rows else None

    def update_job(self, job_id: str, fields: dict) -> None:
        self.client.table("jobs").update(fields).eq("id", job_id).execute()

    def claim_next_job(self) -> dict | None:
        """Take the oldest queued job, safely (see claim_next_job in the SQL schema)."""
        rows = self.client.rpc("claim_next_job").execute().data
        return rows[0] if rows else None

    def requeue_stuck_jobs(self) -> int:
        """Put jobs that were mid-way when the worker stopped back in the queue."""
        rows = (
            self.client.table("jobs")
            .update({"status": "queued", "progress_message": "Waiting to continue"})
            .in_("status", ACTIVE_STATUSES).execute().data
        )
        for row in rows:
            self.update_book(row["book_id"], {"status": "queued"})
        return len(rows)

    def queue_position(self, job: dict) -> int:
        """1 = next in line. Counts the queued jobs that were queued earlier."""
        ahead = (
            self.client.table("jobs").select("id", count="exact")
            .eq("status", "queued").lt("queued_at", job["queued_at"]).execute().count
        )
        return (ahead or 0) + 1

    # ------------------------------------------------------------ chunks

    def get_chunks(self, book_id: str) -> list[dict]:
        return (
            self.client.table("chunks").select("*")
            .eq("book_id", book_id).order("chunk_index").execute().data
        )

    def replace_chunks(self, book_id: str, rows: list[dict]) -> None:
        self.client.table("chunks").delete().eq("book_id", book_id).execute()
        if rows:
            self.client.table("chunks").insert([{**row, "book_id": book_id} for row in rows]).execute()

    def update_chunk(self, book_id: str, chunk_index: int, fields: dict) -> None:
        (
            self.client.table("chunks").update(fields)
            .eq("book_id", book_id).eq("chunk_index", chunk_index).execute()
        )

    # ------------------------------------------------------------ summaries

    def get_summary(self, book_id: str) -> dict | None:
        rows = self.client.table("summaries").select("*").eq("book_id", book_id).execute().data
        return rows[0] if rows else None

    def save_summary(self, book_id: str, markdown: str, model: str) -> None:
        self.client.table("summaries").upsert(
            {"book_id": book_id, "markdown": markdown, "model": model}, on_conflict="book_id"
        ).execute()

    def delete_summary(self, book_id: str) -> None:
        self.client.table("summaries").delete().eq("book_id", book_id).execute()

    # ------------------------------------------------------------ file storage

    def upload_file(self, path: str, data: bytes, content_type: str) -> None:
        self.client.storage.from_(BUCKET).upload(path, data, {"content-type": content_type})

    def download_file(self, path: str) -> bytes:
        return self.client.storage.from_(BUCKET).download(path)

    def delete_file(self, path: str) -> None:
        self.client.storage.from_(BUCKET).remove([path])

    # ------------------------------------------------------------ health

    def ping(self) -> None:
        """Raises if the database can't be reached."""
        self.client.table("books").select("id").limit(1).execute()
