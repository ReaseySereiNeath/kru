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
from tests.conftest import FakeServer


def http_error(status, message="error", headers=None):
    request = httpx2.Request("POST", "http://test/v1/chat/completions")
    response = httpx2.Response(status, request=request, headers=headers or {})
    cls = {429: openai.RateLimitError, 404: openai.NotFoundError,
           400: openai.BadRequestError, 500: openai.InternalServerError}[status]
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
