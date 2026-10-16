"""Send prompts to the language model, safely.

Every model call in Kru goes through LLMClient.chat(), which:
1. Estimates the prompt's size and refuses to send it if it might not fit
   the context (Ollama would silently cut it off instead of failing).
2. Retries failed calls up to 3 times, waiting longer each time
   (and much longer after HTTP 429 "too many requests").
3. Stops right away with a clear message when a free daily limit is used up.
4. Logs the time and token usage of each call.
5. Refuses paid services unless LLM_ALLOW_PAID=true.

Most services are reached with the `openai` package, which works with any
OpenAI-compatible server: Ollama (the default), OpenRouter, Groq, Gemini.
Claude is reached with Anthropic's own `anthropic` package, which supports
Claude's features properly (for example reusing the system prompt across
calls, which makes a book cheaper). LLM_PROVIDER in .env picks the service.
"""

import json
import logging
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import anthropic
import openai

from app.config import Settings, settings
from pipeline.extract import count_words

log = logging.getLogger("kru.llm")

# English text averages about 1.3 tokens per word; 1.4 leaves some margin.
TOKENS_PER_WORD = 1.4
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 2  # then 4, 8
RATE_LIMIT_WAIT_SECONDS = 20  # then 40, 80 (unless the server says how long)
# A local model writing a long final summary can take several minutes.
REQUEST_TIMEOUT_SECONDS = 900
# Claude thinks before it answers, and the thinking counts toward the answer
# limit (max_tokens). This extra room keeps the thinking from cutting the
# answer short. Thinking is billed like the answer.
CLAUDE_THINKING_ROOM = 8000
# Claude models that support Anthropic's server-side refusal fallback: if a
# safety check declines a request, it is re-run on another Claude model.
# (Claude Haiku 5.5 has no fallback.)
CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# The two packages each have their own error classes; we treat them alike.
CONNECTION_ERRORS = (openai.APIConnectionError, anthropic.APIConnectionError)  # incl. timeouts
STATUS_ERRORS = (openai.APIStatusError, anthropic.APIStatusError)


def estimate_tokens(text: str) -> int:
    return math.ceil(count_words(text) * TOKENS_PER_WORD)


def tokens_to_words(tokens: int) -> int:
    return int(tokens / TOKENS_PER_WORD)


def ollama_root_url(base_url: str) -> str:
    """'http://localhost:11434/v1' -> 'http://localhost:11434'."""
    url = base_url.rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def ollama_loaded_context(base_url: str, model: str) -> int | None:
    """Return the context length Ollama loaded for `model`.

    Returns None if this is not an Ollama server or the model is not loaded.
    """
    try:
        with urllib.request.urlopen(ollama_root_url(base_url) + "/api/ps", timeout=5) as r:
            data = json.load(r)
    except (urllib.error.URLError, ValueError):
        return None
    for loaded in data.get("models", []):
        if loaded.get("name") == model or loaded.get("model") == model:
            return loaded.get("context_length")
    return None


class LLMError(Exception):
    """A model call failed. The message is safe to show to the user."""


class PromptTooLong(LLMError):
    pass


class DailyLimitReached(LLMError):
    pass


class ContextTooSmall(LLMError):
    pass


class _EmptyReply(Exception):
    pass


class _Refused(Exception):
    """Claude's safety checks declined the request."""

    def __init__(self, category: str | None):
        super().__init__(category or "")
        self.category = category


@dataclass
class LLMReply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float


@dataclass
class _Answer:
    """What one request returned, in the same shape for every service."""
    text: str
    cut_short: bool  # the answer hit the length limit
    prompt_tokens: int | None
    completion_tokens: int | None


def clean_reply(text: str) -> str:
    # Some open-source "thinking" models put their reasoning in <think> tags
    # before the answer. We only want the answer.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


class LLMClient:
    def __init__(self, cfg: Settings = settings, client=None, sleep=time.sleep):
        self.cfg = cfg
        self.is_claude = cfg.llm_provider == "anthropic"
        # max_retries=0: we do our own retries below, with our own waits.
        if client is not None:
            self.client = client  # tests pass a fake
        elif self.is_claude:
            self.client = anthropic.Anthropic(
                api_key=cfg.llm_api_key, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS
            )
        else:
            self.client = openai.OpenAI(
                base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS,
            )
        self.sleep = sleep  # tests pass a fake so they don't really wait
        self._context_checked: set[str] = set()

    def chat(
        self, model: str, system: str, user: str, *, json_mode: bool = False, final: bool = False
    ) -> LLMReply:
        """Send one prompt and return the reply.

        final=True is for the final summary: it keeps more room for the answer.
        """
        problem = self.cfg.cost_problem()
        if problem:
            raise LLMError(problem)  # never send a request that would cost money unasked

        budget = self.cfg.prompt_budget(final)
        estimated = estimate_tokens(system) + estimate_tokens(user)
        if estimated > budget:
            # The pipeline sizes everything to avoid this; it's a last safety net.
            raise PromptTooLong(
                f"Prompt is about {estimated:,} tokens, over the {budget:,}-token budget."
            )

        # The answer may use the room we reserved for it, and no more.
        answer_room = (
            self.cfg.llm_final_answer_reserve_tokens if final else self.cfg.llm_answer_reserve_tokens
        )
        use_json_mode = json_mode

        for attempt in range(MAX_RETRIES + 1):
            started = time.perf_counter()
            try:
                if self.is_claude:
                    answer = self._send_claude(model, system, user, answer_room)
                else:
                    answer = self._send_openai(model, system, user, answer_room, use_json_mode)
                if not answer.text:
                    raise _EmptyReply()
                break
            except Exception as error:
                if use_json_mode and _rejects_json_mode(error):
                    # Some free models don't offer JSON mode. Ask again without
                    # it: the prompt itself asks for JSON, and the book check
                    # cleans up the reply before reading it.
                    log.warning("This model doesn't support JSON mode; asking without it.")
                    use_json_mode = False
                    continue
                wait = self._retry_wait(error, attempt)
                if wait is None or attempt == MAX_RETRIES:
                    friendly = self._friendly_error(error)
                    if friendly is None:
                        raise  # a bug, not a model problem: keep the original error
                    raise friendly from error
                log.warning(
                    "Model call failed (%s). Retry %d of %d in %d s.",
                    _describe(error), attempt + 1, MAX_RETRIES, wait,
                )
                self.sleep(wait)

        seconds = time.perf_counter() - started
        reply = LLMReply(
            text=answer.text,
            model=model,
            prompt_tokens=answer.prompt_tokens if answer.prompt_tokens is not None else estimated,
            completion_tokens=(answer.completion_tokens if answer.completion_tokens is not None
                               else estimate_tokens(answer.text)),
            seconds=seconds,
        )
        if answer.cut_short:
            log.warning("The reply hit the answer length limit and was cut short.")
        log.info(
            "%s: %.1f s, %s tokens in, %s out",
            model, seconds, f"{reply.prompt_tokens:,}", f"{reply.completion_tokens:,}",
        )
        self._check_context(model)
        return reply

    # ------------------------------------------------------------ sending one request

    def _send_openai(self, model: str, system: str, user: str, answer_room: int,
                     json_mode: bool) -> _Answer:
        request = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self.cfg.llm_temperature,
            max_tokens=answer_room,
        )
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**request)
        choice = response.choices[0]
        usage = response.usage
        return _Answer(
            text=clean_reply(choice.message.content or ""),
            cut_short=choice.finish_reason == "length",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )

    def _send_claude(self, model: str, system: str, user: str, answer_room: int) -> _Answer:
        request = dict(
            model=model,
            max_tokens=answer_room + CLAUDE_THINKING_ROOM,
            # The system prompt is the same for every call of a book, so we
            # ask Claude to cache it: repeated reads cost a tenth of the price.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            # No temperature: current Claude models only accept their default.
            # No JSON mode either: the book check prompt asks for JSON and its
            # reply is cleaned up before reading.
        )
        if self.cfg.llm_effort:
            request["output_config"] = {"effort": self.cfg.llm_effort}
        if model in CLAUDE_FALLBACK_MODELS:
            response = self.client.beta.messages.create(
                **request, betas=[CLAUDE_FALLBACK_BETA], fallbacks="default"
            )
        else:
            response = self.client.messages.create(**request)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise _Refused(getattr(details, "category", None))
        # The reply is a list of blocks; thinking blocks are skipped, text kept.
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        prompt_tokens = (usage.input_tokens + (usage.cache_read_input_tokens or 0)
                         + (usage.cache_creation_input_tokens or 0))
        return _Answer(
            text=clean_reply(text),
            cut_short=response.stop_reason == "max_tokens",
            prompt_tokens=prompt_tokens,
            completion_tokens=usage.output_tokens,  # includes the thinking
        )

    # ------------------------------------------------------------ errors

    def _retry_wait(self, error: Exception, attempt: int) -> float | None:
        """Seconds to wait before retrying, or None if retrying won't help."""
        if isinstance(error, (*CONNECTION_ERRORS, _EmptyReply)):
            return RETRY_WAIT_SECONDS * 2**attempt
        if isinstance(error, STATUS_ERRORS):
            if error.status_code == 429:
                if _is_daily_limit(error):
                    return None  # waiting a few minutes won't help
                retry_after = error.response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    return int(retry_after)
                return RATE_LIMIT_WAIT_SECONDS * 2**attempt
            # 5xx includes Claude's 529 "overloaded".
            if error.status_code >= 500 or error.status_code == 408:
                return RETRY_WAIT_SECONDS * 2**attempt
        return None  # e.g. 400 bad request, 401 wrong key, 404 unknown model, a refusal

    def _friendly_error(self, error: Exception) -> LLMError | None:
        """An error with a message for users, or None if we don't know this error."""
        name = self.cfg.llm_provider_name
        if isinstance(error, CONNECTION_ERRORS):
            if self.cfg.llm_is_local:
                return LLMError(
                    f"Cannot reach the AI model at {self.cfg.llm_base_url}. "
                    "Is Ollama running? Try: brew services start ollama"
                )
            return LLMError(
                f"Cannot reach {name} at {self.cfg.llm_base_url}. "
                "Check the internet connection, or whether the service is down."
            )
        if isinstance(error, _EmptyReply):
            return LLMError("The AI model kept sending empty replies.")
        if isinstance(error, _Refused):
            reason = f" (safety check: {error.category})" if error.category else ""
            return LLMError(
                f"{name} declined to work on part of this book{reason}. "
                "Try a different Claude model in backend/.env, or another AI service."
            )
        if isinstance(error, STATUS_ERRORS):
            if error.status_code == 429 and _is_daily_limit(error):
                return DailyLimitReached(
                    f"The free daily limit of {name} has been reached. Progress is saved; "
                    "try again tomorrow and Kru will continue where it stopped."
                )
            if error.status_code == 429:
                return LLMError(f"{name} is busy (too many requests). Please try again later.")
            if is_bad_api_key(error):
                return LLMError(f"{name} did not accept the API key. Check LLM_API_KEY in backend/.env.")
            if error.status_code == 404:
                return LLMError(f"The AI model was not found on {name}. "
                                "Check the model names in backend/.env.")
            if error.status_code == 413:
                # Groq's free tier, for one, limits how much text one request may hold.
                return LLMError(
                    f"{name} says one request is too large for its free tier. Lower "
                    "CHUNK_TARGET_WORDS and LLM_CONTEXT_TOKENS in backend/.env, then try again."
                )
            if error.status_code == 402 or "credit" in f"{error.message}".lower():
                return LLMError(f"{name} says the account has no credit left. Add credit, "
                                "or switch to a free service in backend/.env.")
            return LLMError(f"{name} returned an error: {error.status_code} {error.message}")
        return None

    def _check_context(self, model: str) -> None:
        """Once per model: make sure Ollama really loaded a big enough context.

        Ollama's OpenAI-compatible endpoint ignores the context size we ask
        for, and if a prompt is bigger than the loaded context it silently
        drops the start. We can't check before the model is loaded, so we
        check right after its first call, before that reply is used.
        """
        if not self.cfg.llm_is_local or model in self._context_checked:
            return  # online services report errors instead of cutting prompts silently
        self._context_checked.add(model)
        loaded = ollama_loaded_context(self.cfg.llm_base_url, model)
        if loaded is not None and loaded < self.cfg.llm_context_tokens:
            raise ContextTooSmall(
                f"Ollama loaded {model} with a {loaded:,}-token context, but "
                f"LLM_CONTEXT_TOKENS is {self.cfg.llm_context_tokens:,}. Long prompts "
                "would be silently cut off. See 'Context length' in the README."
            )


def _is_daily_limit(error) -> bool:
    # Each service words it differently:
    #   OpenRouter: "Rate limit exceeded: free-models-per-day"
    #   Groq:       "Rate limit reached ... on tokens per day (TPD)"
    #   Gemini:     "... quota metric ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    text = f"{error.message} {error.body}".lower()
    return any(words in text for words in ("per-day", "per day", "perday", "daily"))


def is_bad_api_key(error) -> bool:
    # Most services answer 401 or 403; Gemini answers 400 "Please pass a valid API key".
    if error.status_code in (401, 403):
        return True
    return error.status_code == 400 and "api key" in f"{error.message} {error.body}".lower()


def _rejects_json_mode(error: Exception) -> bool:
    """Did the service refuse the request because of JSON mode?"""
    if not isinstance(error, STATUS_ERRORS) or error.status_code not in (400, 422):
        return False
    if is_bad_api_key(error):
        return False
    text = f"{error.message} {error.body}".lower()
    return "response_format" in text or "json" in text


def _describe(error: Exception) -> str:
    if isinstance(error, STATUS_ERRORS):
        return f"HTTP {error.status_code}"
    if isinstance(error, _EmptyReply):
        return "empty reply"
    return type(error).__name__
