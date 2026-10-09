from app.config import load_settings
from pipeline.check_llm import ollama_root_url


def test_defaults_match_plan(monkeypatch):
    # Clear anything set in the shell or .env so we see the real defaults.
    for name in ["LLM_CONTEXT_TOKENS", "LLM_ANSWER_RESERVE_TOKENS",
                 "LLM_FINAL_ANSWER_RESERVE_TOKENS", "CHUNK_TARGET_WORDS"]:
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.llm_context_tokens == 16384
    assert s.chunk_target_words == 4000


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("CHUNK_TARGET_WORDS", "2500")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.1")
    s = load_settings()
    assert s.chunk_target_words == 2500
    assert s.llm_temperature == 0.1


def test_final_budget_reserves_more_room(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "16384")
    monkeypatch.setenv("LLM_ANSWER_RESERVE_TOKENS", "2500")
    monkeypatch.setenv("LLM_FINAL_ANSWER_RESERVE_TOKENS", "4500")
    s = load_settings()
    assert s.prompt_budget() == 13884
    assert s.prompt_budget(final=True) == 11884


def test_ollama_root_url():
    assert ollama_root_url("http://localhost:11434/v1") == "http://localhost:11434"
    assert ollama_root_url("http://localhost:11434/v1/") == "http://localhost:11434"
    assert ollama_root_url("http://localhost:11434") == "http://localhost:11434"
