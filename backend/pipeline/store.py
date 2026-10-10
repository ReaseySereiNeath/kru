"""Save pipeline progress so a crashed or stopped run can continue.

The pipeline saves after every step: the book check, the chunk plan, each
chunk's notes, and the final summary. When a run starts again, it skips
whatever is already saved.

For now progress is kept in a JSON file per book (in backend/runs/). In
Phase 4 a Supabase version with the same methods replaces it, so the
pipeline code stays the same.
"""

import hashlib
import json
import os
from pathlib import Path

from app.config import BACKEND_DIR

RUNS_DIR = BACKEND_DIR / "runs"


def file_fingerprint(path: Path) -> str:
    """A short id from the file's contents, so renaming the file doesn't matter."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


class JsonStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = self._empty()

    @classmethod
    def for_book(cls, book_path: Path) -> "JsonStore":
        return cls(RUNS_DIR / f"{file_fingerprint(book_path)}.json")

    @staticmethod
    def _empty() -> dict:
        return {"book_check": None, "plan": None, "chunks": {}, "calls": [], "summary": None}

    def _save(self) -> None:
        # Write to a temporary file, then swap it in. If the computer stops
        # halfway through writing, the old file is still whole.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def reset(self) -> None:
        self.data = self._empty()
        self._save()

    # --- book check (runs once per book)
    @property
    def book_check(self) -> dict | None:
        return self.data["book_check"]

    def save_book_check(self, check: dict) -> None:
        self.data["book_check"] = check
        self._save()

    # --- chunk plan: one entry per chunk, saved before summarizing starts
    @property
    def plan(self) -> list[dict] | None:
        return self.data["plan"]

    def save_plan(self, plan: list[dict]) -> None:
        """Save a new plan. Notes from an older, different plan no longer fit, so they go."""
        self.data["plan"] = plan
        self.data["chunks"] = {}
        self.data["summary"] = None
        self._save()

    # --- finished chunks
    @property
    def chunk_results(self) -> dict[int, dict]:
        # JSON object keys are always strings; turn them back into numbers.
        return {int(k): v for k, v in self.data["chunks"].items()}

    def save_chunk_result(self, index: int, notes: str, carryover: str, seconds: float) -> None:
        self.data["chunks"][str(index)] = {"notes": notes, "carryover": carryover, "seconds": seconds}
        self._save()

    # --- one entry per model call, for time and token totals
    @property
    def calls(self) -> list[dict]:
        return self.data["calls"]

    def add_call(self, step: str, model: str, seconds: float, prompt_tokens: int,
                 completion_tokens: int) -> None:
        self.data["calls"].append({
            "step": step, "model": model, "seconds": round(seconds, 2),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        })
        self._save()

    # --- the final summary
    @property
    def summary(self) -> dict | None:
        return self.data["summary"]

    def save_summary(self, markdown: str, model: str) -> None:
        self.data["summary"] = {"markdown": markdown, "model": model}
        self._save()
