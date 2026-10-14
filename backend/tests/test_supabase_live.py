"""Tests against a REAL local Supabase (the SQL schema, security rules, storage).

They are skipped unless SUPABASE_URL points at this computer and Supabase is
running (`supabase start`). They never run against a cloud project. Each
test creates its own users and deletes them (and their books) afterwards.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from supabase import create_client

from app.config import settings
from app.db import Database
from app.db_store import DbStore

LOCAL = urlparse(settings.supabase_url).hostname in ("127.0.0.1", "localhost")


def _reachable() -> bool:
    try:
        Database().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (LOCAL and settings.supabase_service_role_key and _reachable()),
    reason="local Supabase is not running (start it with `supabase start`)",
)


class User:
    """A real test user, with a client that is logged in as them (like the browser)."""

    def __init__(self, db: Database):
        email = f"test-{uuid.uuid4().hex[:12]}@example.com"
        created = db.client.auth.admin.create_user(
            {"email": email, "password": "test-password-123", "email_confirm": True})
        self.id = created.user.id
        # The browser uses the ANON key plus the user's login.
        self.client = create_client(settings.supabase_url, settings.supabase_anon_key)
        session = self.client.auth.sign_in_with_password(
            {"email": email, "password": "test-password-123"}).session
        self.token = session.access_token


@pytest.fixture
def db():
    return Database()


@pytest.fixture
def users(db):
    made = [User(db), User(db)]
    yield made
    for user in made:
        for book in db.list_books(user.id):
            if book["file_path"]:
                db.delete_file(book["file_path"])
        db.client.auth.admin.delete_user(user.id)  # their books go too (cascade)


def new_book(db, user, **extra):
    book_id = str(uuid.uuid4())
    book = db.insert_book({"id": book_id, "user_id": user.id, "title": "Test book",
                           "file_type": "txt", **extra})
    db.insert_job(book_id)
    return book


# ---------------------------------------------------------------- login tokens


def test_token_gives_the_user_id(db, users):
    assert db.user_id_for_token(users[0].token) == users[0].id
    assert db.user_id_for_token("not-a-real-token") is None
    assert db.user_id_for_token(settings.supabase_anon_key) is None


# ---------------------------------------------------------------- the database layer


def test_book_job_chunks_summary_round_trip(db, users):
    book = new_book(db, users[0])
    assert book["status"] == "queued" and book["author"] == "Unknown"
    assert db.get_job(book["id"])["status"] == "queued"

    store = DbStore(db, book["id"])
    assert store.plan is None and store.book_check is None and store.summary is None
    store.save_book_info("Real Title", "An Author", 1234, 3)
    store.save_book_check({"book_type": "fiction", "subject": "novel", "reader_level": "beginner",
                           "chapters": ["One", "Two"], "skip_sections": ["Index"]})
    plan = [{"chapter_titles": ["One"], "word_count": 700},
            {"chapter_titles": ["Two (part 1 of 2)", "Two (part 2 of 2)"], "word_count": 534}]
    store.save_plan(plan)
    assert store.plan == plan  # must compare equal, or a resumed job would start over
    assert store.chunk_results == {}
    store.save_chunk_result(0, "notes one", "carry one", 12.345)
    assert store.chunk_results == {0: {"notes": "notes one", "carryover": "carry one", "seconds": 12.35}}
    store.add_call("chunk 1", "m", 12.3, 100, 50)
    store.add_call("chunk 2", "m", 1.2, 10, 5)
    store.save_summary("# Summary", "some-model")
    store.save_summary("# Summary v2", "some-model")  # saving twice replaces, not duplicates

    saved = db.get_book(book["id"])
    assert (saved["title"], saved["author"], saved["word_count"]) == ("Real Title", "An Author", 1234)
    assert saved["book_type"] == "fiction"
    assert saved["book_check"]["chapters"] == ["One", "Two"]
    assert saved["total_tokens"] == 165 and saved["total_seconds"] == 13.5
    assert store.summary == {"markdown": "# Summary v2", "model": "some-model"}

    # A new plan throws away notes and summary that no longer match.
    store.save_plan(plan[:1])
    assert store.chunk_results == {} and store.summary is None

    db.delete_book(book["id"])
    assert db.get_book(book["id"]) is None
    assert db.get_job(book["id"]) is None and db.get_chunks(book["id"]) == []


def test_file_storage(db, users):
    path = f"{users[0].id}/{uuid.uuid4()}.txt"
    db.upload_file(path, b"book text", "text/plain")
    assert db.download_file(path) == b"book text"
    db.delete_file(path)
    with pytest.raises(Exception):
        db.download_file(path)


def test_upload_log(db, users):
    anna, ben = users
    db.log_upload(anna.id)
    db.log_upload(anna.id)
    db.log_upload(ben.id)
    times = db.upload_times_since(anna.id, "2000-01-01T00:00:00+00:00")
    assert len(times) == 2 and times == sorted(times)
    assert db.upload_times_since(anna.id, "2999-01-01T00:00:00+00:00") == []
    # Users can't read or clear the log themselves.
    try:
        rows = anna.client.table("uploads").select("*").execute().data
    except Exception:
        rows = []
    assert rows == []
    try:
        anna.client.table("uploads").delete().eq("user_id", anna.id).execute()
    except Exception:
        pass
    assert len(db.upload_times_since(anna.id, "2000-01-01T00:00:00+00:00")) == 2


def test_job_status_must_be_a_known_one(db, users):
    book = new_book(db, users[0])
    with pytest.raises(Exception):
        db.update_job(db.get_job(book["id"])["id"], {"status": "banana"})


# ---------------------------------------------------------------- the job queue


def test_claim_takes_oldest_first_and_updates_the_job(db, users):
    first, second = new_book(db, users[0]), new_book(db, users[1])
    job = db.get_job(second["id"])
    assert db.queue_position(db.get_job(first["id"])) < db.queue_position(job)

    claimed = [db.claim_next_job(), db.claim_next_job()]
    ours = [c for c in claimed if c and c["book_id"] in (first["id"], second["id"])]
    assert [c["book_id"] for c in ours] == [first["id"], second["id"]]
    assert ours[0]["status"] == "extracting" and ours[0]["attempts"] == 1
    assert db.get_job(first["id"])["updated_at"] > db.get_job(first["id"])["created_at"]

    # Mid-way jobs go back in the queue when the worker restarts.
    assert db.requeue_stuck_jobs() >= 2
    assert db.get_job(first["id"])["status"] == "queued"


def test_two_workers_never_get_the_same_job(db, users):
    books = [new_book(db, users[0]) for _ in range(6)]
    ours = {b["id"] for b in books}

    def claim(_):
        job = Database().claim_next_job()  # each "worker" has its own connection
        return job["book_id"] if job else None

    with ThreadPoolExecutor(max_workers=12) as pool:
        claimed = [c for c in pool.map(claim, range(12)) if c in ours]
    assert sorted(claimed) == sorted(ours)  # every job taken exactly once


# ---------------------------------------------------------------- security rules (RLS)


def test_user_reads_only_their_own_rows(db, users):
    anna, ben = users
    annas, bens = new_book(db, anna), new_book(db, ben)
    for book in (annas, bens):
        DbStore(db, book["id"]).save_plan([{"chapter_titles": [], "word_count": 1}])
        db.save_summary(book["id"], "# Private", "m")

    for table, key in [("books", "id"), ("jobs", "book_id"), ("chunks", "book_id"),
                       ("summaries", "book_id")]:
        seen = {row[key] for row in anna.client.table(table).select("*").execute().data}
        assert seen == {annas["id"]}, table
        # Asking for Ben's row by id returns nothing either.
        assert anna.client.table(table).select("*").eq(key, bens["id"]).execute().data == []


def test_visitor_who_is_not_logged_in_sees_nothing(db, users):
    new_book(db, users[0])
    visitor = create_client(settings.supabase_url, settings.supabase_anon_key)
    for table in ["books", "jobs", "chunks", "summaries"]:
        try:
            rows = visitor.table(table).select("*").execute().data
        except Exception:
            rows = []  # "permission denied" is just as good as "no rows"
        assert rows == [], table


def test_user_cannot_change_data_directly(db, users):
    anna, ben = users
    annas, bens = new_book(db, anna), new_book(db, ben)

    def attempt(action):
        """Run a direct write from the browser side. It must not change anything."""
        try:
            return action().data
        except Exception:
            return []

    # Not even her own book: every change must go through the API.
    assert attempt(lambda: anna.client.table("books").insert(
        {"user_id": anna.id, "title": "Sneaky", "file_type": "txt"}).execute()) == []
    assert attempt(lambda: anna.client.table("books").update(
        {"status": "done"}).eq("id", annas["id"]).execute()) == []
    assert attempt(lambda: anna.client.table("books").delete().eq("id", bens["id"]).execute()) == []
    assert attempt(lambda: anna.client.table("jobs").update(
        {"status": "done"}).eq("book_id", annas["id"]).execute()) == []
    assert attempt(lambda: anna.client.table("summaries").insert(
        {"book_id": annas["id"], "markdown": "fake", "model": "m"}).execute()) == []

    assert db.get_book(annas["id"])["status"] == "queued"
    assert db.get_book(bens["id"]) is not None
    assert db.get_job(annas["id"])["status"] == "queued"
    assert db.get_summary(annas["id"]) is None
    assert len(db.list_books(anna.id)) == 1


def test_user_cannot_claim_jobs_or_touch_files(db, users):
    anna, ben = users
    new_book(db, ben)
    with pytest.raises(Exception):
        anna.client.rpc("claim_next_job").execute()
    assert db.get_job(db.list_books(ben.id)[0]["id"])["status"] == "queued"

    path = f"{ben.id}/{uuid.uuid4()}.txt"
    db.upload_file(path, b"ben's book", "text/plain")
    db.update_book(db.list_books(ben.id)[0]["id"], {"file_path": path})
    with pytest.raises(Exception):
        anna.client.storage.from_("books").download(path)
    with pytest.raises(Exception):
        anna.client.storage.from_("books").upload(f"{anna.id}/x.txt", b"x")
    assert db.download_file(path) == b"ben's book"
