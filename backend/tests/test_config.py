import pytest

from app.config import load_settings
from pipeline.llm import ollama_root_url


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


# ---------------------------------------------------------------- AI service choice


def test_ollama_is_the_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    s = load_settings()
    assert s.llm_provider == "ollama" and s.llm_is_local
    assert s.llm_base_url == "http://localhost:11434/v1"


@pytest.mark.parametrize("provider, url", [
    ("openrouter", "https://openrouter.ai/api/v1"),
    ("groq", "https://api.groq.com/openai/v1"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),  # any case
])
def test_online_services_use_their_own_address(monkeypatch, provider, url):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    # An old Ollama address left in .env must not win.
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    s = load_settings()
    assert s.llm_base_url == url
    assert not s.llm_is_local


def test_ollama_on_another_computer(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_BASE_URL", "http://192.168.1.20:11434/v1")
    assert load_settings().llm_base_url == "http://192.168.1.20:11434/v1"


def test_custom_needs_an_address(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="needs LLM_BASE_URL"):
        load_settings()
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    assert load_settings().llm_base_url == "http://localhost:1234/v1"


def test_unknown_service_is_refused(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt")
    with pytest.raises(ValueError, match="one of: ollama, openrouter, groq, gemini, anthropic, custom"):
        load_settings()


def test_openrouter_allows_only_free_models(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_MODEL_CHUNK", "google/gemma-4-31b-it:free")
    monkeypatch.setenv("LLM_MODEL_FINAL", "google/gemma-4-31b-it:free")
    assert load_settings().cost_problem() is None
    monkeypatch.setenv("LLM_MODEL_FINAL", "openai/gpt-4o")
    problem = load_settings().cost_problem()
    assert "openai/gpt-4o" in problem and "':free'" in problem and "LLM_ALLOW_PAID" in problem
    monkeypatch.setenv("LLM_ALLOW_PAID", "true")
    s = load_settings()
    assert s.cost_problem() is None and s.llm_is_paid


def test_other_services_have_no_model_rule(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL_CHUNK", "some-model")
    assert load_settings().cost_problem() is None


def test_paid_is_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_ALLOW_PAID", raising=False)
    assert load_settings().llm_allow_paid is False


def test_claude_needs_permission_to_spend_money(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_ALLOW_PAID", "")
    s = load_settings()
    assert s.llm_is_paid and "LLM_ALLOW_PAID=true" in s.cost_problem()
    assert s.llm_provider_name == "Claude (Anthropic)"
    for yes in ["true", "TRUE", "1", "yes"]:
        monkeypatch.setenv("LLM_ALLOW_PAID", yes)
        assert load_settings().cost_problem() is None


def test_free_services_are_not_paid(monkeypatch):
    for provider in ["ollama", "groq", "gemini"]:
        monkeypatch.setenv("LLM_PROVIDER", provider)
        assert not load_settings().llm_is_paid


def test_effort(monkeypatch):
    monkeypatch.setenv("LLM_EFFORT", "Low")
    assert load_settings().llm_effort == "low"
    monkeypatch.setenv("LLM_EFFORT", "turbo")
    with pytest.raises(ValueError, match="low, medium, or high"):
        load_settings()
