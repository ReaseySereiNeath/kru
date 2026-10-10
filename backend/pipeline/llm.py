"""Send prompts to the language model, safely.

Every model call in Kru goes through LLMClient.chat(), which:
1. Estimates the prompt's size and refuses to send it if it might not fit
   the context (Ollama would silently cut it off instead of failing).
2. Retries failed calls up to 3 times, waiting longer each time
   (and much longer after HTTP 429 "too many requests").
3. Stops right away with a clear message when a free daily limit is used up.
4. Logs the time and token usage of each call.

It uses the `openai` package, which works with any OpenAI-compatible server:
Ollama (default) or OpenRouter, set by LLM_BASE_URL in .env.
"""

import logging
import math
import re
import time
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, OpenAI

from app.config import Settings, settings
from pipeline.check_llm import ollama_loaded_context
from pipeline.extract import count_words

log = logging.getLogger("kru.llm")

# English text averages about 1.3 tokens per word; 1.4 leaves some margin.
TOKENS_PER_WORD = 1.4
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 2  # then 4, 8
RATE_LIMIT_WAIT_SECONDS = 20  # then 40, 80 (unless the server says how long)
# A local model writing a long final summary can take several minutes.
REQUEST_TIMEOUT_SECONDS = 900


def estimate_tokens(text: str) -> int:
    return math.ceil(count_words(text) * TOKENS_PER_WORD)


def tokens_to_words(tokens: int) -> int:
    return int(tokens / TOKENS_PER_WORD)


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


@dataclass
class LLMReply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float


def clean_reply(text: str) -> str:
    # Some open-source "thinking" models put their reasoning in <think> tags
    # before the answer. We only want the answer.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


class LLMClient:
    def __init__(self, cfg: Settings = settings, client=None, sleep=time.sleep):
        self.cfg = cfg
        # max_retries=0: we do our own retries below, with our own waits.
        self.client = client or OpenAI(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            max_retries=0,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        self.sleep = sleep  # tests pass a fake so they don't really wait
        self._context_checked: set[str] = set()

    def chat(
        self, model: str, system: str, user: str, *, json_mode: bool = False, final: bool = False
    ) -> LLMReply:
        """Send one prompt and return the reply.

        final=True is for the final summary: it keeps more room for the answer.
        """
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
        request = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self.cfg.llm_temperature,
            max_tokens=answer_room,
        )
        if json_mode:
            request["response_format"] = {"type": "json_object"}

        for attempt in range(MAX_RETRIES + 1):
            started = time.perf_counter()
            try:
                response = self.client.chat.completions.create(**request)
                choice = response.choices[0]
                text = clean_reply(choice.message.content or "")
                if not text:
                    raise _EmptyReply()
                break
            except Exception as error:
                wait = self._retry_wait(error, attempt)
                if wait is None or attempt == MAX_RETRIES:
                    raise self._friendly_error(error) from error
                log.warning(
                    "Model call failed (%s). Retry %d of %d in %d s.",
                    _describe(error), attempt + 1, MAX_RETRIES, wait,
                )
                self.sleep(wait)

        seconds = time.perf_counter() - started
        usage = response.usage
        reply = LLMReply(
            text=text,
            model=model,
            prompt_tokens=usage.prompt_tokens if usage else estimated,
            completion_tokens=usage.completion_tokens if usage else estimate_tokens(text),
            seconds=seconds,
        )
        if choice.finish_reason == "length":
            log.warning("The reply hit the %d-token answer limit and was cut short.", answer_room)
        log.info(
            "%s: %.1f s, %s tokens in, %s out",
            model, seconds, f"{reply.prompt_tokens:,}", f"{reply.completion_tokens:,}",
        )
        self._check_context(model)
        return reply

    def _retry_wait(self, error: Exception, attempt: int) -> float | None:
        """Seconds to wait before retrying, or None if retrying won't help."""
        if isinstance(error, (APIConnectionError, _EmptyReply)):  # includes timeouts
            return RETRY_WAIT_SECONDS * 2**attempt
        if isinstance(error, APIStatusError):
            if error.status_code == 429:
                if _is_daily_limit(error):
                    return None  # waiting a few minutes won't help
                retry_after = error.response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    return int(retry_after)
                return RATE_LIMIT_WAIT_SECONDS * 2**attempt
            if error.status_code >= 500 or error.status_code == 408:
                return RETRY_WAIT_SECONDS * 2**attempt
        return None  # e.g. 400 bad request, 401 wrong key, 404 unknown model

    def _friendly_error(self, error: Exception) -> LLMError:
        if isinstance(error, APIConnectionError):
            return LLMError(
                f"Cannot reach the AI model at {self.cfg.llm_base_url}. "
                "Is Ollama running? Try: brew services start ollama"
            )
        if isinstance(error, _EmptyReply):
            return LLMError("The AI model kept sending empty replies.")
        if isinstance(error, APIStatusError):
            if error.status_code == 429 and _is_daily_limit(error):
                return DailyLimitReached(
                    "The free daily limit for the AI service has been reached. "
                    "Progress is saved; try again tomorrow and Kru will continue where it stopped."
                )
            if error.status_code == 429:
                return LLMError("The AI service is busy (too many requests). Please try again later.")
            if error.status_code == 404:
                return LLMError("The AI model was not found. Check the model names in backend/.env.")
            return LLMError(f"The AI service returned an error: {error.status_code} {error.message}")
        return LLMError(f"Model call failed: {error}")

    def _check_context(self, model: str) -> None:
        """Once per model: make sure Ollama really loaded a big enough context.

        Ollama's OpenAI-compatible endpoint ignores the context size we ask
        for, and if a prompt is bigger than the loaded context it silently
        drops the start. We can't check before the model is loaded, so we
        check right after its first call, before that reply is used.
        """
        if model in self._context_checked:
            return
        self._context_checked.add(model)
        loaded = ollama_loaded_context(self.cfg.llm_base_url, model)
        if loaded is not None and loaded < self.cfg.llm_context_tokens:
            raise ContextTooSmall(
                f"Ollama loaded {model} with a {loaded:,}-token context, but "
                f"LLM_CONTEXT_TOKENS is {self.cfg.llm_context_tokens:,}. Long prompts "
                "would be silently cut off. See 'Context length' in the README."
            )


def _is_daily_limit(error: APIStatusError) -> bool:
    # OpenRouter says e.g. "Rate limit exceeded: free-models-per-day".
    text = f"{error.message} {error.body}".lower()
    return "per-day" in text or "per day" in text or "daily" in text


def _describe(error: Exception) -> str:
    if isinstance(error, APIStatusError):
        return f"HTTP {error.status_code}"
    if isinstance(error, _EmptyReply):
        return "empty reply"
    return type(error).__name__
