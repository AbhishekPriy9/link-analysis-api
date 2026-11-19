import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_model: str = "gemini-3.5-flash"
    llm_timeout_seconds: float = 75.0
    fetch_timeout_seconds: float = 15.0
    max_content_chars: int = 12000
    max_response_bytes: int = 3_000_000
    max_redirects: int = 5
    cache_ttl_seconds: int = 900
    user_agent: str = (
        "LinkAnalysisBot/1.0 (+https://github.com/AbhishekPriy9/link-analysis-api)"
    )

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        return cls(
            gemini_api_key=api_key or None,
            gemini_model=_env_str("GEMINI_MODEL", cls.gemini_model),
            gemini_fallback_model=_env_str(
                "GEMINI_FALLBACK_MODEL", cls.gemini_fallback_model
            ),
            llm_timeout_seconds=_env_float(
                "LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds
            ),
            fetch_timeout_seconds=_env_float(
                "FETCH_TIMEOUT_SECONDS", cls.fetch_timeout_seconds
            ),
            max_content_chars=_env_int("MAX_CONTENT_CHARS", cls.max_content_chars),
            max_response_bytes=_env_int(
                "MAX_RESPONSE_BYTES", cls.max_response_bytes
            ),
            max_redirects=_env_int("MAX_REDIRECTS", cls.max_redirects),
            cache_ttl_seconds=_env_int(
                "CACHE_TTL_SECONDS", cls.cache_ttl_seconds
            ),
            user_agent=_env_str("USER_AGENT", cls.user_agent),
        )
