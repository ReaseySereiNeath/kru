"""The Kru API.

Run from the backend folder:
    uvicorn app.main:app --reload --port 8000
Then open http://localhost:8000/docs to try the endpoints.

The API never summarizes a book itself: that can take half an hour, far too
long for a web request. Uploading a book only saves the file and creates a
"queued" job. The worker (python -m worker.run) does the work, and the
website asks GET /books/{id} every few seconds to show progress.

Every endpoint except /health needs the user's Supabase login token:
    Authorization: Bearer <access token>
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI

from app.config import settings
from app.db import Database, now_iso
from pipeline.extract import SUPPORTED_TYPES

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
    "txt": "text/plain",
}

# Room for the form wrapping around the file in an upload request.
UPLOAD_OVERHEAD_BYTES = 1024 * 1024

app = FastAPI(title="Kru API", description="Your book teacher")


@app.middleware("http")
async def refuse_huge_uploads(request: Request, call_next):
    """Say no to an oversized upload before receiving it.

    The browser tells us the size up front (the Content-Length header).
    Without this check, the server would first store the whole upload, even
    several gigabytes, and only then find out it is too big.
    """
    if request.method == "POST" and request.url.path == "/books":
        limit = settings.max_file_mb * 1024 * 1024 + UPLOAD_OVERHEAD_BYTES
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > limit:
            return JSONResponse(
                {"detail": f"This file is too big. The limit is {settings.max_file_mb} MB."},
                status_code=413,
            )
    return await call_next(request)


# Added after the check above so that it wraps it: every answer, including
# the "too big" one, gets the headers that let the website read it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@lru_cache
def get_db() -> Database:
    """One shared database connection, created on first use."""
    return Database()


def current_user(authorization: str | None = Header(None), db: Database = Depends(get_db)) -> str:
    """Return the logged-in user's id, or answer 401 (not logged in)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please log in.")
    user_id = db.user_id_for_token(authorization[7:].strip())
    if not user_id:
        raise HTTPException(401, "Your login has expired. Please log in again.")
    return user_id


def own_book(book_id: str, user_id: str, db: Database) -> dict:
    """Load a book, but only if it belongs to this user.

    Someone else's book gets the same 404 as a book that doesn't exist, so
    nobody can find out which ids are real.
    """
    try:
        uuid.UUID(book_id)
    except ValueError:
        raise HTTPException(404, "Book not found.")
    book = db.get_book(book_id)
    if not book or book["user_id"] != user_id:
        raise HTTPException(404, "Book not found.")
    return book


# ---------------------------------------------------------------- what we send back


def book_json(book: dict) -> dict:
    """The parts of a book row that are safe and useful to send to the browser."""
    keys = ["id", "title", "author", "file_type", "word_count", "page_estimate", "book_type",
            "subject", "reader_level", "status", "total_tokens", "total_seconds", "created_at"]
    return {key: book[key] for key in keys}


def job_json(job: dict, db: Database) -> dict:
    out = {
        "status": job["status"],
        "progress_current": job["progress_current"],
        "progress_total": job["progress_total"],
        "progress_message": job["progress_message"],
        "error": job["error"],
        "attempts": job["attempts"],
        "queue_position": None,
        "seconds_per_chunk": None,
        "eta_seconds": None,
        "updated_at": job["updated_at"],
    }
    if job["status"] == "queued":
        out["queue_position"] = db.queue_position(job)
    elif job["status"] == "summarizing":
        chunks = db.get_chunks(job["book_id"])
        times = [c["seconds_taken"] for c in chunks if c["status"] == "done" and c["seconds_taken"]]
        if times:
            average = sum(times) / len(times)
            remaining = sum(1 for c in chunks if c["status"] != "done")
            out["seconds_per_chunk"] = round(average, 1)
            # + 1: the final summary takes about as long as one more chunk.
            out["eta_seconds"] = round(average * (remaining + 1))
    return out


def check_daily_limit(user_id: str, db: Database) -> None:
    """Answer 429 (too many requests) if the user has used up today's uploads."""
    limit = settings.books_per_day
    if limit <= 0:
        return
    now = datetime.now(timezone.utc)
    times = db.upload_times_since(user_id, (now - timedelta(hours=24)).isoformat())
    if len(times) < limit:
        return
    # A new upload is allowed once fewer than `limit` uploads are younger than
    # 24 hours. That happens when this one turns 24 hours old:
    frees_up = datetime.fromisoformat(times[-limit]) + timedelta(hours=24)
    minutes = max(1, round((frees_up - now).total_seconds() / 60))
    wait = f"{minutes} minutes" if minutes < 90 else f"about {round(minutes / 60)} hours"
    books = "1 book" if limit == 1 else f"{limit} books"
    raise HTTPException(
        429, f"You can upload {books} per day. You can upload again in {wait}."
    )


def title_from_filename(filename: str) -> str:
    title = re.sub(r"[_\-]+", " ", Path(filename).stem).strip()
    return title[:200] or "Untitled"


# ---------------------------------------------------------------- endpoints


@app.post("/books", status_code=201)
def upload_book(file: UploadFile, user_id: str = Depends(current_user),
                db: Database = Depends(get_db)) -> dict:
    """Upload a book. Saves the file and puts a job in the queue."""
    check_daily_limit(user_id, db)
    filename = Path(file.filename or "").name  # .name drops any folder part
    file_type = Path(filename).suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        raise HTTPException(400, "Please upload a PDF, EPUB, or TXT file.")

    # Read one byte more than the limit: enough to know it's too big,
    # without loading a huge file into memory.
    max_bytes = settings.max_file_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"This file is too big. The limit is {settings.max_file_mb} MB.")
    if not data:
        raise HTTPException(400, "This file is empty.")

    book_id = str(uuid.uuid4())
    # The user id is the folder, so files of different users never mix.
    file_path = f"{user_id}/{book_id}.{file_type}"
    db.upload_file(file_path, data, CONTENT_TYPES[file_type])
    try:
        book = db.insert_book({
            "id": book_id, "user_id": user_id, "title": title_from_filename(filename),
            "file_type": file_type, "file_path": file_path, "status": "queued",
        })
        job = db.insert_job(book_id)
    except Exception:
        # Don't leave a file (or a book without a job) behind.
        db.delete_file(file_path)
        db.delete_book(book_id)
        raise
    db.log_upload(user_id)  # counts toward the daily limit, even if the book is deleted later
    return {**book_json(book), "job": job_json(job, db)}


@app.get("/books")
def list_books(user_id: str = Depends(current_user), db: Database = Depends(get_db)) -> list[dict]:
    """The user's books, newest first."""
    return [book_json(book) for book in db.list_books(user_id)]


@app.get("/books/{book_id}")
def get_book(book_id: str, user_id: str = Depends(current_user),
             db: Database = Depends(get_db)) -> dict:
    """One book with its job: status, progress, queue position, time left."""
    book = own_book(book_id, user_id, db)
    job = db.get_job(book_id)
    return {**book_json(book), "job": job_json(job, db) if job else None}


@app.get("/books/{book_id}/summary")
def get_summary(book_id: str, user_id: str = Depends(current_user),
                db: Database = Depends(get_db)) -> dict:
    book = own_book(book_id, user_id, db)
    summary = db.get_summary(book_id)
    if not summary or book["status"] != "done":
        raise HTTPException(404, "The summary is not ready yet.")
    return {
        "book": book_json(book),
        "markdown": summary["markdown"],
        "model": summary["model"],
        "created_at": summary["created_at"],
    }


@app.post("/books/{book_id}/retry")
def retry_book(book_id: str, user_id: str = Depends(current_user),
               db: Database = Depends(get_db)) -> dict:
    """Put a failed job back in the queue. It continues from the last finished chunk."""
    book = own_book(book_id, user_id, db)
    job = db.get_job(book_id)
    if not job or job["status"] != "failed":
        raise HTTPException(409, "Only a failed book can be retried.")
    if not book["file_path"]:
        raise HTTPException(409, "The file is no longer stored. Please upload the book again.")
    db.update_job(job["id"], {
        "status": "queued", "error": None, "queued_at": now_iso(),
        "progress_message": "Waiting to retry",
    })
    db.update_book(book_id, {"status": "queued"})
    return {**book_json(db.get_book(book_id)), "job": job_json(db.get_job(book_id), db)}


@app.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: str, user_id: str = Depends(current_user),
                db: Database = Depends(get_db)) -> Response:
    """Delete a book with its file, chunks, and summary."""
    book = own_book(book_id, user_id, db)
    if book["file_path"]:
        db.delete_file(book["file_path"])
    db.delete_book(book_id)
    return Response(status_code=204)


@app.get("/health")
def health(response: Response) -> dict:
    """Is the database reachable? Is the AI model server reachable? No login needed."""
    result = {"database": "ok", "llm": "ok"}
    try:
        get_db().ping()
    except Exception as error:
        result["database"] = f"error: {type(error).__name__}"
    try:
        OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
               timeout=5, max_retries=0).models.list()
    except Exception as error:
        result["llm"] = f"error: {type(error).__name__}"
    if result != {"database": "ok", "llm": "ok"}:
        response.status_code = 503
    return result
