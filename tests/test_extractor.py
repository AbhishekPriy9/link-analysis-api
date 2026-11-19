import asyncio
import socket
from datetime import UTC

import httpcore
import httpx
import pytest

from app.services.website_extractor import (
    FetchError,
    PinnedBackend,
    WebsiteExtractor,
)
from app.utils.validators import InvalidUrlError

PUBLIC_URL = "http://93.184.216.34/"


def _extractor(
    transport: httpx.AsyncBaseTransport,
    *,
    max_content_chars: int = 12000,
    max_response_bytes: int = 3_000_000,
    max_redirects: int = 5,
    fetch_timeout_seconds: float = 5.0,
) -> WebsiteExtractor:
    return WebsiteExtractor(
        fetch_timeout_seconds=fetch_timeout_seconds,
        max_response_bytes=max_response_bytes,
        max_content_chars=max_content_chars,
        max_redirects=max_redirects,
        user_agent="TestBot/1.0",
        transport=transport,
    )


def _html(body: str) -> str:
    return (
        f"<html><head><title>Acme Web Studio</title></head>"
        f"<body>{body}</body></html>"
    )


async def test_extract_reads_metadata_headings_and_main_text() -> None:
    requests: list[httpx.Request] = []
    page = """<html><head>
      <title>Acme Web Studio</title>
      <meta name="description" content="Web design for small teams.">
      <meta property="og:title" content="Ignored title">
      <script>window.secret = "tracking-id";</script>
      <style>.hero { color: red; }</style>
    </head><body>
      <nav>Home Pricing Contact</nav>
      <main>
        <h1>Acme Web Studio</h1>
        <h2>Services</h2>
        <h2>Services</h2>
        <h3>Web design</h3>
        <p>We build fast websites for small teams.</p>
      </main>
      <footer>Copyright 2026</footer>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=page,
        )

    content = await _extractor(httpx.MockTransport(handler)).extract(PUBLIC_URL)

    assert content.url == PUBLIC_URL
    assert content.final_url == PUBLIC_URL
    assert content.title == "Acme Web Studio"
    assert content.meta_description == "Web design for small teams."
    assert content.headings == ["Acme Web Studio", "Services", "Web design"]
    assert "We build fast websites for small teams." in content.text
    assert "tracking-id" not in content.text
    assert "color: red" not in content.text
    assert "Home Pricing Contact" not in content.text
    assert content.fetched_at.tzinfo is UTC
    assert requests[0].headers["user-agent"] == "TestBot/1.0"
    assert requests[0].headers["accept"] == "text/html,application/xhtml+xml"


async def test_extract_falls_back_to_open_graph_fields() -> None:
    page = """<html><head>
      <meta property="og:title" content="Acme From OG">
      <meta property="og:description" content="Open Graph description.">
    </head><body><p>Body text.</p></body></html>"""

    content = await _extractor(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, text=page
            )
        )
    ).extract(PUBLIC_URL)

    assert content.title == "Acme From OG"
    assert content.meta_description == "Open Graph description."


async def test_extract_truncates_on_a_word_boundary() -> None:
    page = _html("<main><p>alpha beta gamma delta epsilon</p></main>")
    content = await _extractor(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, text=page
            )
        ),
        max_content_chars=12,
    ).extract(PUBLIC_URL)

    assert content.text == "alpha beta"


async def test_extract_stops_reading_at_the_byte_limit() -> None:
    page = _html("<main><p>" + ("a" * 5000) + "</p></main>")
    content = await _extractor(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, text=page
            )
        ),
        max_response_bytes=100,
    ).extract(PUBLIC_URL)

    assert content.text
    assert len(content.text) <= 100
    assert set(content.text) == {"a"}


async def test_extract_follows_public_redirects() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/next"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=_html("<main><p>Final page.</p></main>"),
        )

    content = await _extractor(httpx.MockTransport(handler)).extract(
        f"{PUBLIC_URL}start"
    )

    assert requests == [f"{PUBLIC_URL}start", f"{PUBLIC_URL}next"]
    assert content.final_url == f"{PUBLIC_URL}next"
    assert content.text == "Final page."


async def test_extract_rejects_redirect_to_private_address() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with pytest.raises(InvalidUrlError):
        await _extractor(httpx.MockTransport(handler)).extract(PUBLIC_URL)

    assert requests == [PUBLIC_URL]


async def test_extract_rejects_a_redirect_without_a_location() -> None:
    with pytest.raises(FetchError, match="Location"):
        await _extractor(
            httpx.MockTransport(lambda request: httpx.Response(302))
        ).extract(PUBLIC_URL)


async def test_extract_rejects_a_redirect_to_another_scheme() -> None:
    with pytest.raises(InvalidUrlError):
        await _extractor(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    302, headers={"location": "ftp://example.com/file"}
                )
            )
        ).extract(PUBLIC_URL)


async def test_extract_rejects_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(FetchError, match="too many times"):
        await _extractor(httpx.MockTransport(handler), max_redirects=2).extract(
            PUBLIC_URL
        )


async def test_extract_rejects_error_status() -> None:
    with pytest.raises(FetchError, match="404"):
        await _extractor(
            httpx.MockTransport(lambda request: httpx.Response(404, text="missing"))
        ).extract(PUBLIC_URL)


async def test_extract_rejects_non_html_content() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/pdf"},
        content=b"%PDF-1.7",
    )

    with pytest.raises(FetchError, match="unsupported"):
        await _extractor(
            httpx.MockTransport(lambda request: response)
        ).extract(PUBLIC_URL)


async def test_extract_caps_metadata_lengths() -> None:
    page = (
        "<html><head><title>"
        + ("t" * 1000)
        + '</title><meta name="description" content="'
        + ("d" * 2000)
        + '"></head><body><h1>'
        + ("h" * 1000)
        + "</h1><p>Body.</p></body></html>"
    )

    content = await _extractor(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, text=page
            )
        )
    ).extract(PUBLIC_URL)

    assert len(content.title) <= 300
    assert len(content.meta_description) <= 500
    assert len(content.headings[0]) <= 200


async def test_extract_times_out_on_a_slow_server() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, text=_html("<main><p>Too late.</p></main>"))

    with pytest.raises(FetchError, match="too long"):
        await _extractor(
            httpx.MockTransport(handler), fetch_timeout_seconds=0.01
        ).extract(PUBLIC_URL)


async def test_extract_accepts_missing_content_type() -> None:
    content = await _extractor(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=_html("<main><p>Plain response.</p></main>").encode()
            )
        )
    ).extract(PUBLIC_URL)

    assert content.text == "Plain response."


async def test_extract_uses_the_meta_charset_when_the_header_omits_it() -> None:
    page = (
        "<html><head><meta charset='shift_jis'><title>テスト</title></head>"
        "<body><p>日本語の本文です。</p></body></html>"
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=page.encode("shift_jis"),
    )

    content = await _extractor(
        httpx.MockTransport(lambda request: response)
    ).extract(PUBLIC_URL)

    assert content.title == "テスト"
    assert "日本語の本文です" in content.text


async def test_extract_reports_dns_failure_as_fetch_error(monkeypatch) -> None:
    def failing_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)

    with pytest.raises(FetchError, match="resolved"):
        await _extractor(
            httpx.MockTransport(
                lambda request: httpx.Response(200, text=_html("<p>x</p>"))
            )
        ).extract("http://does-not-resolve.example/")


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        self.calls.append((host, port))
        return object()

    async def connect_unix_socket(self, *args, **kwargs):
        return object()

    async def sleep(self, seconds: float) -> None:
        return None


async def test_pinned_backend_connects_to_the_validated_address() -> None:
    delegate = RecordingBackend()
    backend = PinnedBackend(delegate)
    backend.pin("example.com", 80, ["93.184.216.34"])

    await backend.connect_tcp("example.com", 80)

    assert delegate.calls == [("93.184.216.34", 80)]


async def test_pinned_backend_tries_the_next_validated_address() -> None:
    class FlakyBackend(RecordingBackend):
        async def connect_tcp(self, host, port, **kwargs):
            self.calls.append((host, port))
            if host == "93.184.216.34":
                raise httpcore.ConnectError("unreachable")
            return object()

    delegate = FlakyBackend()
    backend = PinnedBackend(delegate)
    backend.pin("example.com", 80, ["93.184.216.34", "2606:4700::1"])

    await backend.connect_tcp("example.com", 80)

    assert delegate.calls == [
        ("93.184.216.34", 80),
        ("2606:4700::1", 80),
    ]
