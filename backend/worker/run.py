"""The background worker: takes queued jobs and summarizes the books.

Run from the backend folder (in its own terminal, next to the API):
    python -m worker.run

What it does, forever:
1. Ask the database for the oldest queued job (claim_next_job). If there
   is none, wait a few seconds and ask again.
2. Download the book file, run the summary pipeline, and save progress to
   the database as it goes, so the website can show it.
3. Mark the job done or failed, and delete the uploaded file when finished.

If the worker stops mid-book (Ctrl+C, crash, restart), the job is put back
in the queue the next time the worker starts, and continues from the last
finished chunk.

Run only ONE worker process. To summarize several books at the same time,
set WORKER_CONCURRENCY instead (with one local model, 1 is usually fastest).
"""

import logging
import tempfile
import threading
import time
from pathlib import Path

from app.config import Settings, settings
from app.db import Database
from app.db_store import DbStore
from pipeline.extract import ExtractionError
from pipeline.llm import LLMClient, LLMError
from pipeline.summarize import BookRejected, Progress, summarize_book

log = logging.getLogger("kru.worker")

POLL_SECONDS = 3
UNEXPECTED_ERROR = "Something went wrong while summarizing this book. Please try again."


def process_job(db: Database, llm: LLMClient, job: dict, cfg: Settings = settings) -> str:
    """Summarize the book of one claimed job. Returns the job's final status."""
    job_id, book_id = job["id"], job["book_id"]
    book = db.get_book(book_id)
    if book is None:  # the user deleted the book while it was waiting
        return "deleted"

    def set_status(status: str, **job_fields) -> None:
        db.update_job(job_id, {"status": status, **job_fields})
        db.update_book(book_id, {"status": status})

    def on_progress(p: Progress) -> None:
        if p.status == "done":
            return  # we mark it done ourselves below, after cleaning up
        set_status(p.status, progress_current=p.current, progress_total=p.total,
                   progress_message=p.message)

    def delete_uploaded_file() -> None:
        db.delete_file(book["file_path"])
        db.update_book(book_id, {"file_path": None})

    try:
        with tempfile.TemporaryDirectory() as folder:
            # Name the temporary file after the book, because the pipeline uses
            # the filename as the title when the file has no title inside it.
            safe_title = book["title"].replace("/", " ").replace("\\", " ")
            path = Path(folder) / f"{safe_title}.{book['file_type']}"
            path.write_bytes(db.download_file(book["file_path"]))
            summarize_book(path, llm, DbStore(db, book_id), cfg, on_progress)
    except BookRejected as rejected:
        # Too long, or a scanned PDF. Retrying can't help, so the file goes.
        set_status(rejected.status, error=str(rejected), progress_message="")
        delete_uploaded_file()
        return rejected.status
    except (ExtractionError, LLMError) as error:
        # These messages are written for users. The file is kept for a retry.
        set_status("failed", error=str(error))
        return "failed"
    except Exception:
        log.exception("Job %s failed unexpectedly", job_id)
        if db.get_book(book_id) is None:
            return "deleted"  # the book was deleted while we worked on it
        set_status("failed", error=UNEXPECTED_ERROR)
        return "failed"

    delete_uploaded_file()
    total = len(db.get_chunks(book_id))
    set_status("done", progress_current=total, progress_total=total,
               progress_message="Summary ready", error=None)
    return "done"


def work_forever(cfg: Settings, stop: threading.Event, name: str) -> None:
    # Each thread gets its own connections; they aren't safe to share.
    db, llm = Database(cfg), LLMClient(cfg)
    while not stop.is_set():
        try:
            job = db.claim_next_job()
        except Exception:
            log.exception("%s: could not reach the database; trying again soon", name)
            job = None
        if job is None:
            stop.wait(POLL_SECONDS)
            continue
        log.info("%s: started job %s (attempt %s)", name, job["id"], job["attempts"])
        started = time.perf_counter()
        try:
            status = process_job(db, llm, job, cfg)
        except Exception:
            # Even marking the job failed didn't work (database down?). It stays
            # "in progress" and is re-queued the next time the worker starts.
            log.exception("%s: job %s could not be finished", name, job["id"])
            continue
        log.info("%s: job %s -> %s in %.0f s", name, job["id"], status,
                 time.perf_counter() - started)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2"):  # one log line per HTTP request is too much
        logging.getLogger(noisy).setLevel(logging.WARNING)

    requeued = Database(settings).requeue_stuck_jobs()
    if requeued:
        log.info("Put %d unfinished job(s) back in the queue", requeued)

    stop = threading.Event()
    threads = [
        threading.Thread(target=work_forever, args=(settings, stop, f"worker-{i + 1}"), daemon=True)
        for i in range(settings.worker_concurrency)
    ]
    for thread in threads:
        thread.start()
    log.info("Worker running (%d at a time). Press Ctrl+C to stop.", len(threads))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        log.info("Stopping. Unfinished jobs continue when the worker starts again.")


if __name__ == "__main__":
    main()
