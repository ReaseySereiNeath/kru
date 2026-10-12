import re
from dataclasses import replace

import pytest

from pipeline.llm import LLMClient
from tests.conftest import FakeServer
from tests.fakes import FakeDatabase
from tests.test_llm import connection_error
from tests.test_summarize import FakeModel, chunk_prompts
from worker.run import UNEXPECTED_ERROR, process_job


@pytest.fixture
def db():
    return FakeDatabase()


def add_book(db, path, title="uploaded name", user="anna"):
    """What POST /books does: store the file, create the book and its job."""
    file_type = path.suffix.lstrip(".")
    book_id = f"book-{len(db.books) + 1}"
    file_path = f"{user}/{book_id}.{file_type}"
    db.upload_file(file_path, path.read_bytes(), "application/octet-stream")
    db.insert_book({"id": book_id, "user_id": user, "title": title,
                    "file_type": file_type, "file_path": file_path})
    db.insert_job(book_id)
    return book_id


def work(db, cfg, model):
    """Claim the next job and process it, like one turn of the worker loop."""
    server = FakeServer(model)
    llm = LLMClient(cfg, client=server, sleep=lambda s: None)
    status = process_job(db, llm, db.claim_next_job(), cfg)
    return status, server


def test_job_runs_to_done(db, cfg, samples):
    cfg = replace(cfg, chunk_target_words=500)
    book_id = add_book(db, samples["epub"])
    status, _ = work(db, cfg, FakeModel())

    assert status == "done"
    book, job = db.get_book(book_id), db.get_job(book_id)
    assert book["status"] == job["status"] == "done"
    assert (job["progress_current"], job["progress_total"]) == (4, 4)
    assert job["progress_message"] == "Summary ready"
    assert job["attempts"] == 1
    # Facts from the file and from the book check are saved on the book.
    assert book["title"] == "The Little Lighthouse"  # from the file, not the upload name
    assert book["author"] == "Ada Example"
    assert book["word_count"] == 1744 and book["page_estimate"] == 4
    assert (book["book_type"], book["subject"], book["reader_level"]) == (
        "fiction", "seaside novel", "beginner")
    # 6 calls (book check, 4 chunks, final) x 150 fake tokens.
    assert book["total_tokens"] == 900
    assert book["total_seconds"] >= 0
    # The summary and every chunk's notes are saved.
    assert db.get_summary(book_id)["markdown"] == "# Final summary"
    chunks = db.get_chunks(book_id)
    assert [c["status"] for c in chunks] == ["done"] * 4
    assert chunks[0]["carryover"] == "Carry from chunk 1."
    assert chunks[0]["chapter_titles"] == ["Chapter 1: The Storm"]
    # The uploaded file is deleted: we keep only the summary.
    assert db.files == {} and book["file_path"] is None


def test_title_falls_back_to_the_uploaded_name(db, cfg, samples):
    book_id = add_book(db, samples["txt"], title="My Notes")  # a TXT has no title inside
    work(db, cfg, FakeModel())
    assert db.get_book(book_id)["title"] == "My Notes"


def test_progress_is_saved_while_working(db, cfg, samples, monkeypatch):
    cfg = replace(cfg, chunk_target_words=500)
    book_id = add_book(db, samples["epub"])
    seen = []
    real_update = db.update_job
    monkeypatch.setattr(db, "update_job",
                        lambda job_id, fields: (seen.append(dict(fields)), real_update(job_id, fields)))
    work(db, cfg, FakeModel())
    statuses = [f["status"] for f in seen]
    assert statuses[0] == "extracting" and statuses[-1] == "done"
    assert {"analyzing", "summarizing", "finalizing"} <= set(statuses)
    assert "Summarizing part 3 of 4" in [f.get("progress_message") for f in seen]


def test_too_long_book(db, cfg, samples):
    book_id = add_book(db, samples["pdf"])
    status, server = work(db, replace(cfg, max_words=1000), FakeModel())
    assert status == "too_long"
    job = db.get_job(book_id)
    assert job["status"] == db.get_book(book_id)["status"] == "too_long"
    assert "1,000 words" in job["error"]
    assert server.requests == []     # no model call was wasted
    assert db.files == {}            # nothing to retry, so the file is deleted
    assert db.get_book(book_id)["word_count"] == 1744  # we can still show how long it is


def test_model_failure_marks_job_failed_and_keeps_the_file(db, cfg, samples):
    book_id = add_book(db, samples["epub"])

    def always_down(request):
        raise connection_error()
    status, _ = work(db, cfg, always_down)

    assert status == "failed"
    job = db.get_job(book_id)
    assert job["status"] == db.get_book(book_id)["status"] == "failed"
    assert "Is Ollama running" in job["error"]
    assert len(db.files) == 1  # kept, so the user can press retry


def test_unreadable_file_fails_with_a_clear_message(db, cfg, tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_text("not really a pdf")
    book_id = add_book(db, bad)
    status, _ = work(db, cfg, FakeModel())
    assert status == "failed"
    assert "Could not open this PDF" in db.get_job(book_id)["error"]


def test_unexpected_error_gives_a_safe_message(db, cfg, samples):
    book_id = add_book(db, samples["epub"])

    def bug(request):
        raise ZeroDivisionError("internal detail users should not see")
    status, _ = work(db, cfg, bug)
    assert status == "failed"
    assert db.get_job(book_id)["error"] == UNEXPECTED_ERROR


def test_crash_then_restart_resumes_from_last_chunk(db, cfg, samples):
    cfg = replace(cfg, chunk_target_words=500)
    book_id = add_book(db, samples["epub"])
    with pytest.raises(KeyboardInterrupt):  # the worker is killed during chunk 3
        work(db, cfg, FakeModel(crash_on_chunk=3))
    assert db.get_job(book_id)["status"] == "summarizing"  # left "in progress"
    assert db.claim_next_job() is None                     # so nobody would pick it up...

    assert db.requeue_stuck_jobs() == 1                    # ...until the worker starts again
    assert db.get_book(book_id)["status"] == "queued"
    status, server = work(db, cfg, FakeModel())

    assert status == "done"
    assert db.get_job(book_id)["attempts"] == 2
    # No second book check, and only chunks 3 and 4 were summarized.
    assert not any("response_format" in r for r in server.requests)
    prompts = chunk_prompts(server)
    assert [re.search(r"Chunk (\d+) of", p).group(1) for p in prompts] == ["3", "4"]
    assert "Carry from chunk 2." in prompts[0]
    assert db.get_summary(book_id)["markdown"] == "# Final summary"


def test_failed_job_retried_resumes_too(db, cfg, samples):
    cfg = replace(cfg, chunk_target_words=500)
    book_id = add_book(db, samples["epub"])
    calls = {"n": 0}
    model = FakeModel()

    def fails_after_three_calls(request):
        calls["n"] += 1
        if calls["n"] > 3:  # book check + chunks 1 and 2 succeed
            raise connection_error()
        return model(request)
    assert work(db, cfg, fails_after_three_calls)[0] == "failed"

    # What POST /books/{id}/retry does:
    db.update_job(db.get_job(book_id)["id"], {"status": "queued", "error": None})
    status, server = work(db, cfg, FakeModel())
    assert status == "done"
    assert len(chunk_prompts(server)) == 2


def test_jobs_are_claimed_oldest_first_and_only_once(db, cfg, samples):
    first = add_book(db, samples["txt"])
    second = add_book(db, samples["txt"])
    assert db.claim_next_job()["book_id"] == first
    assert db.claim_next_job()["book_id"] == second
    assert db.claim_next_job() is None


def test_book_deleted_while_waiting(db, cfg, samples):
    book_id = add_book(db, samples["txt"])
    job = db.claim_next_job()
    db.delete_book(book_id)
    llm = LLMClient(cfg, client=FakeServer(FakeModel()))
    assert process_job(db, llm, job, cfg) == "deleted"
