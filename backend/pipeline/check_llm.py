"""Check that the LLM is reachable and working.

Run from the backend folder:
    python -m pipeline.check_llm

It does three things:
1. Checks that the models named in LLM_MODEL_CHUNK / LLM_MODEL_FINAL exist.
2. Sends one short test message and prints the reply, time, and tokens.
3. (Ollama only) Checks that the context window Ollama actually loaded is
   at least LLM_CONTEXT_TOKENS. This matters because Ollama's
   OpenAI-compatible endpoint ignores the num_ctx setting we send, and if
   a prompt is longer than the loaded context, Ollama silently drops the
   start of it.
"""

import json
import sys
import time
import urllib.error
import urllib.request

from openai import APIConnectionError, APIStatusError, OpenAI

from app.config import settings


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


def main() -> int:
    print(f"LLM server:   {settings.llm_base_url}")
    print(f"Chunk model:  {settings.llm_model_chunk}")
    print(f"Final model:  {settings.llm_model_final}")
    print(f"Context size: {settings.llm_context_tokens} tokens (LLM_CONTEXT_TOKENS)")
    print()

    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)

    # 1. Do the models exist?
    try:
        available = {m.id for m in client.models.list()}
    except APIConnectionError:
        print("FAIL: cannot reach the LLM server.")
        print("      Is Ollama running? Try: brew services start ollama")
        return 1

    missing = {settings.llm_model_chunk, settings.llm_model_final} - available
    if missing:
        print(f"FAIL: model(s) not found: {', '.join(sorted(missing))}")
        print(f"      Available: {', '.join(sorted(available)) or '(none)'}")
        print("      Download one with: ollama pull <model name>")
        return 1
    print("OK:   models found")

    # 2. Send a test message.
    # The first call can take 10-30 seconds because the model is loaded
    # from disk into memory. Later calls are much faster.
    print("...   sending a test message (first call loads the model, please wait)")
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=settings.llm_model_chunk,
            messages=[
                {"role": "system", "content": "You are Kru, a patient teacher."},
                {"role": "user", "content": "In one sentence, why do people summarize books?"},
            ],
            temperature=settings.llm_temperature,
            max_tokens=100,
        )
    except APIStatusError as error:
        print(f"FAIL: the server returned an error: {error.status_code} {error.message}")
        return 1
    seconds = time.perf_counter() - start

    print(f"OK:   reply in {seconds:.1f} s")
    print(f"      Reply: {response.choices[0].message.content.strip()}")
    if response.usage:
        print(
            f"      Tokens: {response.usage.prompt_tokens} in, "
            f"{response.usage.completion_tokens} out"
        )

    # 3. Is the loaded context big enough? (Ollama only)
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
