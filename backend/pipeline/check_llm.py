"""Check that the LLM is reachable and working.

Run from the backend folder:
    python -m pipeline.check_llm

It works for Ollama and for the online services (LLM_PROVIDER in .env).
It does three things:
1. Checks that the models named in LLM_MODEL_CHUNK / LLM_MODEL_FINAL exist.
2. Sends one short test message and prints the reply, time, and tokens.
3. (Ollama only) Checks that the context window Ollama actually loaded is
   at least LLM_CONTEXT_TOKENS. This matters because Ollama's
   OpenAI-compatible endpoint ignores the num_ctx setting we send, and if
   a prompt is longer than the loaded context, Ollama silently drops the
   start of it.
"""

import sys

import anthropic
import openai

from app.config import settings
from pipeline.llm import (
    CONNECTION_ERRORS,
    STATUS_ERRORS,
    LLMClient,
    LLMError,
    is_bad_api_key,
    ollama_loaded_context,
)


def model_hint(available: set[str]) -> str:
    """A short list of model names to choose from (online services list hundreds)."""
    names = sorted(available)
    if settings.llm_provider == "openrouter" and not settings.llm_allow_paid:
        names = [n for n in names if n.endswith(":free")]  # only free models are allowed
    shown = ", ".join(names[:15]) or "(none)"
    return shown + (f", ... ({len(names) - 15} more)" if len(names) > 15 else "")


def main() -> int:
    print(f"AI service:   {settings.llm_provider_name} ({settings.llm_base_url})")
    print(f"Chunk model:  {settings.llm_model_chunk}")
    print(f"Final model:  {settings.llm_model_final}")
    print(f"Context size: {settings.llm_context_tokens} tokens (LLM_CONTEXT_TOKENS)")
    print()

    problem = settings.cost_problem()
    if problem:
        print(f"FAIL: {problem}")
        return 1
    if settings.llm_is_paid:
        print(f"PAID: {settings.llm_provider_name} charges for every book (LLM_ALLOW_PAID=true).")
        print("      Check the service's prices; the test below costs a fraction of a cent.")
    if not settings.llm_is_local and settings.llm_api_key in ("", "ollama"):
        print(f"FAIL: {settings.llm_provider_name} needs an API key. Set LLM_API_KEY in backend/.env.")
        return 1

    # 1. Do the models exist?
    try:
        if settings.llm_provider == "anthropic":
            client = anthropic.Anthropic(api_key=settings.llm_api_key)
        else:
            client = openai.OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        available = {m.id for m in client.models.list()}
    except CONNECTION_ERRORS:
        print(f"FAIL: cannot reach {settings.llm_provider_name}.")
        if settings.llm_is_local:
            print("      Is Ollama running? Try: brew services start ollama")
        else:
            print("      Check the internet connection.")
        return 1
    except STATUS_ERRORS as error:
        if is_bad_api_key(error):
            print(f"FAIL: {settings.llm_provider_name} did not accept the API key (LLM_API_KEY).")
        else:
            print(f"FAIL: {settings.llm_provider_name} returned {error.status_code} {error.message}")
        return 1

    # Gemini lists its models as "models/<name>"; accept either spelling.
    available |= {name.removeprefix("models/") for name in available}
    missing = {settings.llm_model_chunk, settings.llm_model_final} - available
    if missing:
        print(f"FAIL: model(s) not found: {', '.join(sorted(missing))}")
        print(f"      Available: {model_hint(available)}")
        if settings.llm_is_local:
            print("      Download one with: ollama pull <model name>")
        return 1
    print("OK:   models found")

    # 2. Send a test message.
    # With Ollama, the first call can take 10-30 seconds because the model is
    # loaded from disk into memory. Later calls are much faster.
    if settings.llm_is_local:
        print("...   sending a test message (first call loads the model, please wait)")
    else:
        print("...   sending a test message")
    # Sent the same way books are, so it tests exactly what the worker does.
    try:
        reply = LLMClient(settings).chat(
            settings.llm_model_chunk,
            "You are Kru, a patient teacher.",
            "In one sentence, why do people summarize books?",
        )
    except LLMError as error:
        print(f"FAIL: {error}")
        return 1
    print(f"OK:   reply in {reply.seconds:.1f} s")
    print(f"      Reply: {reply.text}")
    print(f"      Tokens: {reply.prompt_tokens} in, {reply.completion_tokens} out")

    # 3. Is the loaded context big enough? (Ollama only)
    if not settings.llm_is_local:
        print("SKIP: context check (only needed for Ollama)")
        print(f"      Make sure LLM_CONTEXT_TOKENS ({settings.llm_context_tokens}) is not more than "
              "the model's context on this service.")
        print()
        print("All checks passed.")
        return 0
    loaded = ollama_loaded_context(settings.llm_base_url, settings.llm_model_chunk)
    if loaded is None:
        print("SKIP: context check (not an Ollama server, or model not loaded)")
    elif loaded < settings.llm_context_tokens:
        print(
            f"WARN: Ollama loaded a {loaded}-token context, smaller than "
            f"LLM_CONTEXT_TOKENS={settings.llm_context_tokens}."
        )
        print("      Long prompts would be silently cut off.")
        print("      Fix: set OLLAMA_CONTEXT_LENGTH (see README), or lower LLM_CONTEXT_TOKENS.")
        return 1
    else:
        print(f"OK:   Ollama context is {loaded} tokens (needs >= {settings.llm_context_tokens})")

    print()
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
