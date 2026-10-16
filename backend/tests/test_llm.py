from dataclasses import replace

import anthropic
import httpx2  # only to build fake HTTP errors; installed with the openai package
import openai
import pytest

from pipeline.llm import (
    ContextTooSmall,
    DailyLimitReached,
    LLMClient,
    LLMError,
    PromptTooLong,
    clean_reply,
    estimate_tokens,
)
from tests.conftest import FakeClaude, FakeServer


def http_error(status, message="error", headers=None):
    request = httpx2.Request("POST", "http://test/v1/chat/completions")
    response = httpx2.Response(status, request=request, headers=headers or {})
    cls = {429: openai.RateLimitError, 404: openai.NotFoundError, 401: openai.AuthenticationError,
           400: openai.BadRequestError, 413: openai.APIStatusError,
           500: openai.InternalServerError}[status]
    return cls(message, response=response, body={"message": message})


def connection_error():
    return openai.APIConnectionError(request=httpx2.Request("POST", "http://test"))


def failing_then_ok(*errors):
    """A server that raises the given errors in order, then answers 'ok'."""
    errors = list(errors)

    def respond(request):
        if errors:
            raise errors.pop(0)
        return "ok"
    return respond


def make_client(cfg, respond):
    waits = []
    server = FakeServer(respond)
    return LLMClient(cfg, client=server, sleep=waits.append), server, waits


def test_estimate_tokens():
    assert estimate_tokens("one two three four five") == 7  # 5 x 1.4
    assert estimate_tokens("") == 0


def test_clean_reply_removes_thinking():
    assert clean_reply("<think>hmm\nlet me see</think>\n\nAnswer") == "Answer"


def test_reply_and_usage(cfg):
    client, server, _ = make_client(cfg, lambda r: "  Hello  ")
    reply = client.chat("m", "system", "user")
    assert reply.text == "Hello"
    assert (reply.prompt_tokens, reply.completion_tokens) == (100, 50)
    request = server.requests[0]
    assert request["max_tokens"] == 2500  # the answer reserve
    assert "response_format" not in request


def test_json_mode_and_final_reserve(cfg):
    client, server, _ = make_client(cfg, lambda r: "{}")
    client.chat("m", "s", "u", json_mode=True, final=True)
    assert server.requests[0]["response_format"] == {"type": "json_object"}
    assert server.requests[0]["max_tokens"] == 4500


def test_prompt_over_budget_is_never_sent(cfg):
    client, server, _ = make_client(cfg, lambda r: "ok")
    budget_words = int(cfg.prompt_budget() / 1.4)
    client.chat("m", "", "word " * (budget_words - 10))  # fits
    with pytest.raises(PromptTooLong):
        client.chat("m", "", "word " * (budget_words + 10))
    assert len(server.requests) == 1


def test_final_budget_is_smaller(cfg):
    client, _, _ = make_client(cfg, lambda r: "ok")
    words = int(cfg.prompt_budget(final=True) / 1.4) + 100
    client.chat("m", "", "word " * words)  # fits the normal budget
    with pytest.raises(PromptTooLong):
        client.chat("m", "", "word " * words, final=True)


def test_retries_connection_errors_with_growing_waits(cfg):
    client, server, waits = make_client(cfg, failing_then_ok(connection_error(), connection_error()))
    assert client.chat("m", "s", "u").text == "ok"
    assert waits == [2, 4]
    assert len(server.requests) == 3


def test_gives_up_after_three_retries(cfg):
    client, server, waits = make_client(cfg, failing_then_ok(*[connection_error()] * 4))
    with pytest.raises(LLMError, match="Is Ollama running"):
        client.chat("m", "s", "u")
    assert waits == [2, 4, 8]
    assert len(server.requests) == 4  # first try + 3 retries


def test_rate_limit_waits_longer(cfg):
    client, _, waits = make_client(cfg, failing_then_ok(http_error(429), http_error(429)))
    client.chat("m", "s", "u")
    assert waits == [20, 40]


def test_rate_limit_respects_retry_after(cfg):
    error = http_error(429, headers={"retry-after": "7"})
    client, _, waits = make_client(cfg, failing_then_ok(error))
    client.chat("m", "s", "u")
    assert waits == [7]


def test_daily_limit_stops_at_once(cfg):
    error = http_error(429, "Rate limit exceeded: free-models-per-day")
    client, server, waits = make_client(cfg, failing_then_ok(error))
    with pytest.raises(DailyLimitReached, match="try again tomorrow"):
        client.chat("m", "s", "u")
    assert waits == []
    assert len(server.requests) == 1


def test_server_errors_are_retried(cfg):
    client, _, waits = make_client(cfg, failing_then_ok(http_error(500)))
    assert client.chat("m", "s", "u").text == "ok"
    assert waits == [2]


@pytest.mark.parametrize("status, message", [(404, "not found"), (400, "error: 400")])
def test_client_errors_are_not_retried(cfg, status, message):
    client, server, waits = make_client(cfg, failing_then_ok(http_error(status)))
    with pytest.raises(LLMError, match=message):
        client.chat("m", "s", "u")
    assert waits == []


def test_empty_reply_is_retried(cfg):
    replies = iter(["", "<think>only thinking</think>", "real answer"])
    client, _, waits = make_client(cfg, lambda r: next(replies))
    assert client.chat("m", "s", "u").text == "real answer"
    assert waits == [2, 4]


def test_context_too_small_is_caught(cfg, monkeypatch):
    monkeypatch.setattr("pipeline.llm.ollama_loaded_context", lambda url, model: 8192)
    client, _, _ = make_client(cfg, lambda r: "ok")
    with pytest.raises(ContextTooSmall, match="8,192"):
        client.chat("m", "s", "u")


def test_context_checked_once_per_model(cfg, monkeypatch):
    asked = []
    monkeypatch.setattr("pipeline.llm.ollama_loaded_context",
                        lambda url, model: asked.append(model) or 32768)
    client, _, _ = make_client(cfg, lambda r: "ok")
    for model in ["a", "a", "b"]:
        client.chat(model, "s", "u")
    assert asked == ["a", "b"]



# ---------------------------------------------------------------- online AI services


@pytest.fixture
def gemini(cfg):
    return replace(cfg, llm_provider="gemini", llm_api_key="key",
                   llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai/")


def test_paid_openrouter_model_is_never_called(cfg):
    paid = replace(cfg, llm_provider="openrouter", llm_model_chunk="openai/gpt-4o",
                   llm_model_final="openai/gpt-4o")
    client, server, _ = make_client(paid, lambda r: "ok")
    with pytest.raises(LLMError, match="not free"):
        client.chat("openai/gpt-4o", "s", "u")
    assert server.requests == []


def test_json_mode_is_dropped_if_the_model_does_not_support_it(cfg):
    error = http_error(400, "This model does not support response_format json_object")
    client, server, waits = make_client(cfg, failing_then_ok(error))
    assert client.chat("m", "s", "u", json_mode=True).text == "ok"
    assert "response_format" in server.requests[0]
    assert "response_format" not in server.requests[1]
    assert waits == []  # no waiting needed for this


def test_other_bad_requests_are_not_retried_without_json_mode(cfg):
    client, server, _ = make_client(cfg, failing_then_ok(http_error(400, "context length exceeded")))
    with pytest.raises(LLMError):
        client.chat("m", "s", "u")  # not json_mode: nothing to drop
    assert len(server.requests) == 1


@pytest.mark.parametrize("message", [
    "Rate limit exceeded: free-models-per-day",                                    # OpenRouter
    "Rate limit reached for model on tokens per day (TPD): Limit 500000",          # Groq
    "Quota exceeded for quota metric GenerateRequestsPerDayPerProjectPerModel-FreeTier",  # Gemini
])
def test_daily_limits_of_each_service_stop_at_once(gemini, message):
    client, server, waits = make_client(gemini, failing_then_ok(http_error(429, message)))
    with pytest.raises(DailyLimitReached, match="free daily limit of Google Gemini"):
        client.chat("m", "s", "u")
    assert waits == [] and len(server.requests) == 1


@pytest.mark.parametrize("error", [
    http_error(401, "Invalid API Key"),
    http_error(400, "Please pass a valid API key"),  # Gemini's way of saying it
])
def test_bad_api_key(gemini, error):
    client, server, _ = make_client(gemini, failing_then_ok(error))
    with pytest.raises(LLMError, match="Google Gemini did not accept the API key"):
        client.chat("m", "s", "u", json_mode=True)  # a key problem is not a JSON-mode problem
    assert len(server.requests) == 1


def test_request_too_large_for_free_tier(gemini):
    client, _, _ = make_client(gemini, failing_then_ok(http_error(413, "Request too large")))
    with pytest.raises(LLMError, match="Lower\nCHUNK_TARGET_WORDS|CHUNK_TARGET_WORDS"):
        client.chat("m", "s", "u")


def test_connection_error_names_the_service(gemini):
    client, _, _ = make_client(gemini, failing_then_ok(*[connection_error()] * 4))
    with pytest.raises(LLMError) as info:
        client.chat("m", "s", "u")
    assert "Google Gemini" in str(info.value) and "Ollama" not in str(info.value)


def test_no_ollama_context_check_for_online_services(gemini, monkeypatch):
    asked = []
    monkeypatch.setattr("pipeline.llm.ollama_loaded_context",
                        lambda url, model: asked.append(url) or 1)
    client, _, _ = make_client(gemini, lambda r: "ok")
    client.chat("m", "s", "u")
    assert asked == []



# ---------------------------------------------------------------- paid: Claude


def claude_error(status, message="error"):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    cls = {429: anthropic.RateLimitError, 529: anthropic.APIStatusError,
           400: anthropic.BadRequestError, 401: anthropic.AuthenticationError}[status]
    return cls(message, response=response, body={"message": message})


@pytest.fixture
def claude(cfg):
    return replace(cfg, llm_provider="anthropic", llm_base_url="https://api.anthropic.com",
                   llm_api_key="key", llm_allow_paid=True)


def claude_client(cfg, respond):
    waits = []
    server = FakeClaude(respond)
    return LLMClient(cfg, client=server, sleep=waits.append), server, waits


def test_claude_is_never_called_without_permission(claude):
    client, server, _ = claude_client(replace(claude, llm_allow_paid=False), lambda r: "ok")
    with pytest.raises(LLMError, match="paid service"):
        client.chat("claude-haiku-5-5", "s", "u")
    assert server.requests == []


def test_claude_request_shape(claude):
    client, server, _ = claude_client(claude, lambda r: "  The answer.  ")
    reply = client.chat("claude-haiku-5-5", "system prompt", "user text", final=True)
    endpoint, request = server.requests[0]
    assert endpoint == "messages"  # Haiku 5.5 has no refusal fallback
    assert request["model"] == "claude-haiku-5-5"
    assert request["system"] == [{"type": "text", "text": "system prompt",
                                  "cache_control": {"type": "ephemeral"}}]
    assert request["messages"] == [{"role": "user", "content": "user text"}]
    assert request["max_tokens"] == 4500 + 8000  # answer room + thinking room
    assert "temperature" not in request         # current Claude models reject it
    assert "output_config" not in request       # no LLM_EFFORT set
    # Only the text block is kept; thinking blocks are skipped.
    assert reply.text == "The answer."
    # Cached prompt tokens count as prompt tokens too.
    assert (reply.prompt_tokens, reply.completion_tokens) == (940, 300)


def test_claude_effort_and_fallback(claude):
    client, server, _ = claude_client(replace(claude, llm_effort="low"), lambda r: "ok")
    client.chat("claude-sonnet-5-5", "s", "u", json_mode=True)
    endpoint, request = server.requests[0]
    assert endpoint == "beta"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["fallbacks"] == "default"
    assert request["output_config"] == {"effort": "low"}
    assert "response_format" not in request  # Claude has no JSON mode switch


def test_claude_refusal_is_explained_and_not_retried(claude):
    client, server, waits = claude_client(
        claude, lambda r: {"text": "", "stop_reason": "refusal", "category": "general_harms"})
    with pytest.raises(LLMError, match="declined to work on part of this book .safety check: general_harms."):
        client.chat("claude-haiku-5-5", "s", "u")
    assert len(server.requests) == 1 and waits == []


def test_claude_answer_cut_short_still_returned(claude, caplog):
    client, _, _ = claude_client(claude, lambda r: {"text": "partial", "stop_reason": "max_tokens"})
    assert client.chat("claude-haiku-5-5", "s", "u").text == "partial"
    assert "cut short" in caplog.text


@pytest.mark.parametrize("error, wait", [(claude_error(429), 20), (claude_error(529, "Overloaded"), 2)])
def test_claude_busy_errors_are_retried(claude, error, wait):
    client, server, waits = claude_client(claude, failing_then_ok(error))
    assert client.chat("claude-haiku-5-5", "s", "u").text == "ok"
    assert waits == [wait]


def test_claude_bad_key_and_no_credit(claude):
    client, _, _ = claude_client(claude, failing_then_ok(claude_error(401, "invalid x-api-key")))
    with pytest.raises(LLMError, match="did not accept the API key"):
        client.chat("claude-haiku-5-5", "s", "u")
    low = claude_error(400, "Your credit balance is too low to access the Anthropic API.")
    client, _, _ = claude_client(claude, failing_then_ok(low))
    with pytest.raises(LLMError, match="no credit left"):
        client.chat("claude-haiku-5-5", "s", "u")


def test_paid_openrouter_model_works_when_allowed(cfg):
    paid = replace(cfg, llm_provider="openrouter", llm_allow_paid=True,
                   llm_model_chunk="openai/gpt-4o", llm_model_final="openai/gpt-4o")
    client, server, _ = make_client(paid, lambda r: "ok")
    assert client.chat("openai/gpt-4o", "s", "u").text == "ok"
    assert len(server.requests) == 1
