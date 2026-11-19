import pytest

from app.config import Settings


def test_from_env_reads_values_and_keeps_defaults(monkeypatch) -> None:
    monkeypatch.setattr("app.config.load_dotenv", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "  test-key  ")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("MAX_CONTENT_CHARS", "9000")

    settings = Settings.from_env()

    assert settings.gemini_api_key == "test-key"
    assert settings.gemini_model == "gemini-test"
    assert settings.max_content_chars == 9000
    assert settings.cache_ttl_seconds == 900


def test_from_env_treats_a_blank_key_as_missing(monkeypatch) -> None:
    monkeypatch.setattr("app.config.load_dotenv", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "   ")

    assert Settings.from_env().gemini_api_key is None


def test_from_env_rejects_a_non_numeric_number(monkeypatch) -> None:
    monkeypatch.setattr("app.config.load_dotenv", lambda: None)
    monkeypatch.setenv("MAX_CONTENT_CHARS", "not-a-number")

    with pytest.raises(ValueError):
        Settings.from_env()
