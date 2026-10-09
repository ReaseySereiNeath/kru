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


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    # --- LLM ---
    llm_base_url: str
    llm_api_key: str
    llm_model_chunk: str
    llm_model_final: str
    llm_context_tokens: int
    llm_temperature: float
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

    # --- Supabase (used from Phase 4) ---
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    def prompt_budget(self, final: bool = False) -> int:
        """Largest prompt (in tokens) we may send without risking cut-off."""
        reserve = (
            self.llm_final_answer_reserve_tokens
            if final
            else self.llm_answer_reserve_tokens
        )
        return self.llm_context_tokens - reserve


def load_settings() -> Settings:
    return Settings(
        llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        llm_api_key=os.getenv("LLM_API_KEY", "ollama"),
        llm_model_chunk=os.getenv("LLM_MODEL_CHUNK", "qwen2.5:14b"),
        llm_model_final=os.getenv("LLM_MODEL_FINAL", "qwen2.5:14b"),
        llm_context_tokens=_int("LLM_CONTEXT_TOKENS", 16384),
        llm_temperature=_float("LLM_TEMPERATURE", 0.3),
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
    )


settings = load_settings()
