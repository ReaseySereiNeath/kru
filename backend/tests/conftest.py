import pytest

from tests.samples.make_samples import make_all


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    """Fresh sample.pdf / sample.epub / sample.txt, built once per test run."""
    return make_all(tmp_path_factory.mktemp("samples"))


# ---------------------------------------------------------------- a fake model server

from dataclasses import replace
from types import SimpleNamespace

from app.config import settings


class FakeServer:
    """Stands in for the OpenAI client. `respond(request)` returns the reply
    text for each request, or raises an exception to simulate a failure."""

    def __init__(self, respond):
        self.respond = respond
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        text = self.respond(request)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )

    def user_messages(self):
        return [r["messages"][1]["content"] for r in self.requests]


@pytest.fixture
def cfg():
    """Default settings, whatever is in the developer's .env."""
    return replace(
        settings, llm_model_chunk="chunk-model", llm_model_final="final-model",
        llm_context_tokens=16384, llm_answer_reserve_tokens=2500,
        llm_final_answer_reserve_tokens=4500, chunk_target_words=4000,
        chunk_notes_max_words=250, merge_notes_max_words=400,
        max_words=200000, max_file_mb=50,
    )


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    # The context check asks the real Ollama server; tests never should.
    monkeypatch.setattr("pipeline.llm.ollama_loaded_context", lambda base_url, model: None)
