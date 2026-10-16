"""All settings for the backend, read from environment variables.

Values come from the real environment first, then from backend/.env
(copy backend/.env.example to create it). Every other module imports
`settings` from here instead of calling os.getenv itself, so there is
one place to see every setting the app uses.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
PROMPTS_DIR = PROJECT_DIR / "prompts"

# override=False: a variable already set in the shell wins over the .env file.
load_dotenv(BACKEND_DIR / ".env", override=False)


# The AI services Kru knows, and their OpenAI-compatible addresses.
# Ollama runs on this computer; the others are online services with a free tier.
# "custom" is any other OpenAI-compatible server; it needs LLM_BASE_URL.
# "anthropic" (Claude) is the one service that is NOT reached through the
# OpenAI-compatible connection: Kru uses Anthropic's own package for it.
LLM_PROVIDERS = {
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "anthropic": "https://api.anthropic.com",
    "custom": "",
}

# Services that always cost money. Kru only uses them when LLM_ALLOW_PAID=true.
PAID_PROVIDERS = {"anthropic"}

# Names to show people, e.g. in error messages.
LLM_PROVIDER_NAMES = {
    "ollama": "Ollama",
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "gemini": "Google Gemini",
    "anthropic": "Claude (Anthropic)",
    "custom": "the AI server",
}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    # --- LLM ---
    llm_provider: str  # a key of LLM_PROVIDERS
    llm_base_url: str
    llm_api_key: str
    llm_model_chunk: str
    llm_model_final: str
    llm_context_tokens: int
    llm_temperature: float
    # Paid services are refused unless this is True (LLM_ALLOW_PAID=true).
    llm_allow_paid: bool
    # Claude only: how hard the model thinks (low, medium, high). More effort
    # can mean better summaries, and costs more. "" = the model's default.
    llm_effort: str
    # Tokens kept free for the model's answer. The final summary is much
    # longer than chunk notes (up to ~2,000 words plus diagrams), so it
    # gets a bigger reserve.
    llm_answer_reserve_tokens: int
    llm_final_answer_reserve_tokens: int

    # --- Pipeline ---
    chunk_target_words: int
    chunk_notes_max_words: int
    merge_notes_max_words: int
    max_words: int
    max_file_mb: int

    # --- Worker ---
    worker_concurrency: int

    # --- Supabase ---
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # --- API ---
    # The website's address. Browsers only let that site call the API (CORS).
    frontend_origin: str
    # Uploads allowed per user per 24 hours. 0 = no limit.
    books_per_day: int

    @property
    def llm_is_local(self) -> bool:
        return self.llm_provider == "ollama"

    @property
    def llm_provider_name(self) -> str:
        return LLM_PROVIDER_NAMES[self.llm_provider]

    def _paid_openrouter_models(self) -> list[str]:
        # On OpenRouter only model ids ending in ":free" are free.
        if self.llm_provider != "openrouter":
            return []
        return sorted({m for m in (self.llm_model_chunk, self.llm_model_final)
                       if not m.endswith(":free")})

    @property
    def llm_is_paid(self) -> bool:
        """True when every book costs money with these settings."""
        return self.llm_provider in PAID_PROVIDERS or bool(self._paid_openrouter_models())

    def cost_problem(self) -> str | None:
        """Why these settings can't be used, or None if they are fine.

        Free is the default. A paid service is only used when the owner has
        said so with LLM_ALLOW_PAID=true. (Groq and Gemini count as free:
        they only charge when billing is set up on the account.)
        """
        if self.llm_allow_paid:
            return None
        if self.llm_provider in PAID_PROVIDERS:
            return (
                f"{self.llm_provider_name} is a paid service: every book costs money. "
                "To use it anyway, set LLM_ALLOW_PAID=true in backend/.env."
            )
        paid = self._paid_openrouter_models()
        if paid:
            return (
                f"OpenRouter model(s) {', '.join(paid)} are not free. Pick ids that end in "
                "':free', or set LLM_ALLOW_PAID=true in backend/.env to allow paid models."
            )
        return None

    def prompt_budget(self, final: bool = False) -> int:
        """Largest prompt (in tokens) we may send without risking cut-off."""
        reserve = (
            self.llm_final_answer_reserve_tokens
            if final
            else self.llm_answer_reserve_tokens
        )
        return self.llm_context_tokens - reserve


def _effort(value: str) -> str:
    value = value.strip().lower()
    if value not in ("", "low", "medium", "high"):
        raise ValueError(f"LLM_EFFORT={value!r} is not known. Use low, medium, or high (or leave it empty).")
    return value


def load_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower() or "ollama"
    if provider not in LLM_PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER={provider!r} is not known. Use one of: {', '.join(LLM_PROVIDERS)}."
        )
    # The online services always use their own address, so an old LLM_BASE_URL
    # left in .env can't send requests to the wrong place. LLM_BASE_URL is
    # used for Ollama (e.g. Ollama on another computer) and for "custom".
    base_url = LLM_PROVIDERS[provider]
    if provider in ("ollama", "custom"):
        base_url = os.getenv("LLM_BASE_URL", "").strip() or base_url
    if not base_url:
        raise ValueError("LLM_PROVIDER=custom needs LLM_BASE_URL (the server's /v1 address).")
    return Settings(
        llm_provider=provider,
        llm_base_url=base_url,
        llm_api_key=os.getenv("LLM_API_KEY", "").strip() or "ollama",
        llm_model_chunk=os.getenv("LLM_MODEL_CHUNK", "qwen2.5:14b"),
        llm_model_final=os.getenv("LLM_MODEL_FINAL", "qwen2.5:14b"),
        llm_context_tokens=_int("LLM_CONTEXT_TOKENS", 16384),
        llm_temperature=_float("LLM_TEMPERATURE", 0.3),
        llm_allow_paid=os.getenv("LLM_ALLOW_PAID", "").strip().lower() in ("1", "true", "yes"),
        llm_effort=_effort(os.getenv("LLM_EFFORT", "")),
        llm_answer_reserve_tokens=_int("LLM_ANSWER_RESERVE_TOKENS", 2500),
        llm_final_answer_reserve_tokens=_int("LLM_FINAL_ANSWER_RESERVE_TOKENS", 4500),
        chunk_target_words=_int("CHUNK_TARGET_WORDS", 4000),
        chunk_notes_max_words=_int("CHUNK_NOTES_MAX_WORDS", 250),
        merge_notes_max_words=_int("MERGE_NOTES_MAX_WORDS", 400),
        max_words=_int("MAX_WORDS", 200000),
        max_file_mb=_int("MAX_FILE_MB", 50),
        worker_concurrency=_int("WORKER_CONCURRENCY", 1),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"),
        books_per_day=_int("BOOKS_PER_DAY", 3),
    )


settings = load_settings()
