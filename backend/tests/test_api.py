from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from tests.fakes import FakeDatabase

ANNA = {"Authorization": "Bearer anna-token"}
BEN = {"Authorization": "Bearer ben-token"}
SOME_UUID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def db():
    fake = FakeDatabase()
    fake.tokens = {"anna-token": "anna", "ben-token": "ben"}
    return fake


@pytest.fixture
def client(db):
    main.app.dependency_overrides[main.get_db] = lambda: db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def upload(client, headers=ANNA, name="my_first-book.txt", data=b"Some words."):
    return client.post("/books", files={"file": (name, data)}, headers=headers)


# ---------------------------------------------------------------- login


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                     {"Authorization": "anna-token"}])
def test_endpoints_need_a_valid_login(client, headers):
    assert client.get("/books", headers=headers).status_code == 401
    assert client.post("/books", files={"file": ("a.txt", b"x")}, headers=headers).status_code == 401
    assert client.get(f"/books/{SOME_UUID}", headers=headers).status_code == 401
    assert client.delete(f"/books/{SOME_UUID}", headers=headers).status_code == 401


# ---------------------------------------------------------------- upload


def test_upload_saves_file_and_queues_a_job(client, db):
    response = upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "my first book"
    assert body["status"] == "queued"
    assert body["job"]["status"] == "queued"
    assert body["job"]["queue_position"] == 1
    # The file is stored under the user's own folder.
    assert db.files == {f"anna/{body['id']}.txt": b"Some words."}
    # Nothing private leaks into the response.
    assert "file_path" not in body and "user_id" not in body


def test_queue_position_counts_books_ahead(client):
    first = upload(client).json()
    second = upload(client, BEN).json()
    assert second["job"]["queue_position"] == 2
    assert client.get(f"/books/{first['id']}", headers=ANNA).json()["job"]["queue_position"] == 1


@pytest.mark.parametrize("name", ["book.docx", "book", "book.pdf.exe"])
def test_upload_rejects_other_file_types(client, db, name):
    response = upload(client, name=name)
    assert response.status_code == 400
    assert "PDF, EPUB, or TXT" in response.json()["detail"]
    assert db.files == {} and db.books == {}


def test_upload_rejects_big_files(client, db, monkeypatch):
    monkeypatch.setattr(main, "settings", replace(settings, max_file_mb=1))
    assert upload(client, data=b"x" * (1024 * 1024)).status_code == 201  # exactly 1 MB is fine
    response = upload(client, data=b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    assert "limit is 1 MB" in response.json()["detail"]
    assert len(db.books) == 1


def test_upload_rejects_empty_files(client):
    assert upload(client, data=b"").status_code == 400


def test_upload_cleans_up_if_the_database_fails(client, db, monkeypatch):
    def broken(book_id):
        raise RuntimeError("database down")
    monkeypatch.setattr(db, "insert_job", broken)
    with pytest.raises(RuntimeError):
        upload(client)
    assert db.files == {} and db.books == {}


# ---------------------------------------------------------------- reading


def test_list_shows_only_own_books_newest_first(client):
    upload(client, name="one.txt")
    upload(client, BEN, name="bens.txt")
    upload(client, name="two.txt")
    titles = [b["title"] for b in client.get("/books", headers=ANNA).json()]
    assert titles == ["two", "one"]


def test_cannot_see_someone_elses_book(client):
    book_id = upload(client).json()["id"]
    assert client.get(f"/books/{book_id}", headers=ANNA).status_code == 200
    for path in [f"/books/{book_id}", f"/books/{book_id}/summary"]:
        assert client.get(path, headers=BEN).status_code == 404
    assert client.post(f"/books/{book_id}/retry", headers=BEN).status_code == 404
    assert client.delete(f"/books/{book_id}", headers=BEN).status_code == 404
    assert client.get(f"/books/{book_id}", headers=ANNA).status_code == 200  # still there


def test_unknown_or_malformed_id_is_404(client):
    assert client.get(f"/books/{SOME_UUID}", headers=ANNA).status_code == 404
    assert client.get("/books/not-a-uuid", headers=ANNA).status_code == 404


def test_progress_and_time_left_while_summarizing(client, db):
    book_id = upload(client).json()["id"]
    job = db.get_job(book_id)
    db.update_job(job["id"], {"status": "summarizing", "progress_current": 2, "progress_total": 5,
                              "progress_message": "Summarizing part 3 of 5"})
    db.replace_chunks(book_id, [{"chunk_index": i, "chapter_titles": [], "word_count": 100}
                                for i in range(5)])
    db.update_chunk(book_id, 0, {"status": "done", "seconds_taken": 40})
    db.update_chunk(book_id, 1, {"status": "done", "seconds_taken": 60})

    job = client.get(f"/books/{book_id}", headers=ANNA).json()["job"]
    assert job["progress_message"] == "Summarizing part 3 of 5"
    assert (job["progress_current"], job["progress_total"]) == (2, 5)
    assert job["seconds_per_chunk"] == 50
    assert job["eta_seconds"] == 200  # 3 chunks left + the final summary, at 50 s each
    assert job["queue_position"] is None


def test_summary(client, db):
    book_id = upload(client).json()["id"]
    response = client.get(f"/books/{book_id}/summary", headers=ANNA)
    assert response.status_code == 404
    assert "not ready" in response.json()["detail"]

    db.save_summary(book_id, "# Summary", "some-model")
    db.update_book(book_id, {"status": "done"})
    body = client.get(f"/books/{book_id}/summary", headers=ANNA).json()
    assert body["markdown"] == "# Summary"
    assert body["model"] == "some-model"
    assert body["book"]["id"] == book_id


# ---------------------------------------------------------------- retry and delete


def test_retry_only_for_failed_books(client, db):
    book_id = upload(client).json()["id"]
    assert client.post(f"/books/{book_id}/retry", headers=ANNA).status_code == 409


def test_retry_puts_a_failed_job_at_the_back_of_the_queue(client, db):
    failed_id = upload(client).json()["id"]
    waiting_id = upload(client).json()["id"]
    job = db.get_job(failed_id)
    db.update_job(job["id"], {"status": "failed", "error": "The AI model stopped."})
    db.update_book(failed_id, {"status": "failed"})

    body = client.post(f"/books/{failed_id}/retry", headers=ANNA).json()
    assert body["status"] == "queued"
    assert body["job"]["error"] is None
    assert body["job"]["queue_position"] == 2
    assert db.claim_next_job()["book_id"] == waiting_id


def test_retry_needs_the_file(client, db):
    book_id = upload(client).json()["id"]
    db.update_job(db.get_job(book_id)["id"], {"status": "failed"})
    db.update_book(book_id, {"file_path": None})
    response = client.post(f"/books/{book_id}/retry", headers=ANNA)
    assert response.status_code == 409
    assert "upload the book again" in response.json()["detail"]


def test_delete_removes_everything(client, db):
    book_id = upload(client).json()["id"]
    db.save_summary(book_id, "# Summary", "m")
    assert client.delete(f"/books/{book_id}", headers=ANNA).status_code == 204
    assert db.books == {} and db.jobs == {} and db.files == {} and db.summaries == {}
    assert client.get(f"/books/{book_id}", headers=ANNA).status_code == 404


# ---------------------------------------------------------------- health


class FakeOpenAI:
    def __init__(self, **kwargs):
        self.models = self

    def list(self):
        return []


def test_health_ok(client, db, monkeypatch):
    monkeypatch.setattr(main, "get_db", lambda: db)
    monkeypatch.setattr(main, "OpenAI", FakeOpenAI)
    response = client.get("/health")  # no login needed
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "llm": "ok"}


def test_health_reports_what_is_down(client, db, monkeypatch):
    def unreachable():
        raise ConnectionError("no database")
    monkeypatch.setattr(db, "ping", unreachable)
    monkeypatch.setattr(main, "get_db", lambda: db)
    monkeypatch.setattr(main, "OpenAI", FakeOpenAI)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"database": "error: ConnectionError", "llm": "ok"}
