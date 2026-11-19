from app.utils.cache import TtlCache


def test_get_returns_none_after_the_entry_expires(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr("app.utils.cache.time.monotonic", lambda: clock[0])

    cache = TtlCache(ttl_seconds=60)
    cache.set("https://example.com/", {"value": 1})

    clock[0] += 59
    assert cache.get("https://example.com/") == {"value": 1}

    clock[0] += 2
    assert cache.get("https://example.com/") is None


def test_cache_evicts_the_oldest_entry_when_full() -> None:
    cache = TtlCache(ttl_seconds=60, max_entries=2)
    cache.set("first", {"value": 1})
    cache.set("second", {"value": 2})
    cache.set("third", {"value": 3})

    assert cache.get("first") is None
    assert cache.get("second") == {"value": 2}
    assert cache.get("third") == {"value": 3}
