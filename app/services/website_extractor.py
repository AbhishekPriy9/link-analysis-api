import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpcore
import httpx
from bs4 import BeautifulSoup, Tag

from app.utils.validators import (
    DEFAULT_PORTS,
    HostResolutionError,
    assert_public_url,
    normalize_url,
)

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
REMOVED_TAGS = ("script", "style", "noscript", "svg", "iframe", "form", "template")
META_CHARSET = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?([a-zA-Z0-9_\-]+)', re.IGNORECASE
)


class FetchError(Exception):
    pass


@dataclass
class WebsiteContent:
    url: str
    final_url: str
    title: str
    meta_description: str
    headings: list[str]
    text: str
    fetched_at: datetime


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connects to the address that was validated, not a second DNS answer."""

    def __init__(self, delegate: httpcore.AsyncNetworkBackend) -> None:
        self._delegate = delegate
        self._target: tuple[str, int, list[str]] | None = None

    def pin(self, host: str, port: int, ips: list[str]) -> None:
        self._target = (host, port, ips)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> Any:
        targets = [host]
        if (
            self._target is not None
            and host == self._target[0]
            and port == self._target[1]
        ):
            targets = self._target[2]

        last_error: Exception | None = None
        for target in targets:
            try:
                return await self._delegate.connect_tcp(
                    target,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError("No address to connect to.")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> Any:
        return await self._delegate.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._backend = PinnedBackend(httpcore.AnyIOBackend())
        # httpx does not expose the pool's network backend, so swap the pool.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=self._backend,
        )

    def pin(self, host: str, port: int, ips: list[str]) -> None:
        self._backend.pin(host, port, ips)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    boundary = clipped.rfind(" ")
    if boundary > 0:
        clipped = clipped[:boundary]
    return clipped.rstrip()


def _meta_content(soup: BeautifulSoup, attribute: str, value: str) -> str:
    for meta in soup.find_all("meta"):
        raw = meta.get(attribute)
        if isinstance(raw, str) and raw.lower() == value:
            content = meta.get("content")
            if isinstance(content, str) and content.strip():
                return _collapse(content)
    return ""


def _container(soup: BeautifulSoup) -> Tag | None:
    for name in ("main", "article"):
        found = soup.find(name)
        if found is not None:
            return found
    return soup.body


def _header_charset(response: httpx.Response) -> str | None:
    content_type = response.headers.get("content-type", "")
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip("\"'")
            return charset or None
    return None


def _decode_body(body: bytes, header_charset: str | None) -> str:
    encoding = header_charset
    if not encoding:
        match = META_CHARSET.search(body[:4096])
        if match:
            encoding = match.group(1).decode("ascii", "ignore")
    if not encoding:
        encoding = "utf-8"

    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class WebsiteExtractor:
    def __init__(
        self,
        *,
        fetch_timeout_seconds: float,
        max_response_bytes: int,
        max_content_chars: int,
        max_redirects: int,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = fetch_timeout_seconds
        self._max_bytes = max_response_bytes
        self._max_chars = max_content_chars
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._transport = transport

    async def extract(self, url: str) -> WebsiteContent:
        try:
            async with asyncio.timeout(self._timeout):
                html, final_url = await self._fetch(url)
        except TimeoutError as exc:
            raise FetchError("The website took too long to respond.") from exc
        except HostResolutionError as exc:
            raise FetchError("The website host could not be resolved.") from exc
        except httpx.TimeoutException as exc:
            raise FetchError("The website took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise FetchError("The website could not be reached.") from exc

        return await asyncio.to_thread(self._parse, html, url, final_url)

    async def _fetch(self, url: str) -> tuple[str, str]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }
        current = normalize_url(url)
        transport = self._transport or PinnedTransport()

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            headers=headers,
            transport=transport,
            trust_env=False,
        ) as client:
            redirects = 0
            while True:
                validated_ips = await assert_public_url(current)
                if isinstance(transport, PinnedTransport):
                    origin = httpx.URL(current)
                    host = origin.raw_host.decode("ascii")
                    port = origin.port or DEFAULT_PORTS[origin.scheme]
                    transport.pin(host, port, validated_ips)

                async with client.stream("GET", current) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        if redirects >= self._max_redirects:
                            raise FetchError("The website redirected too many times.")
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError(
                                "The website returned a redirect without a "
                                "Location header."
                            )
                        current = normalize_url(urljoin(current, location))
                        redirects += 1
                        continue

                    if response.status_code >= 400:
                        raise FetchError(
                            f"The website returned HTTP {response.status_code}."
                        )
                    if response.status_code >= 300:
                        raise FetchError(
                            "The website returned an unexpected HTTP "
                            f"{response.status_code}."
                        )

                    content_type = response.headers.get("content-type", "")
                    media_type = content_type.split(";", 1)[0].strip().lower()
                    if media_type and media_type not in ALLOWED_CONTENT_TYPES:
                        raise FetchError(
                            "The website returned an unsupported content type."
                        )

                    body = await self._read_body(response)
                    return _decode_body(body, _header_charset(response)), current

    async def _read_body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            remaining = self._max_bytes - total
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            total += min(len(chunk), remaining)
            if total >= self._max_bytes:
                break
        return b"".join(chunks)

    def _parse(self, html: str, url: str, final_url: str) -> WebsiteContent:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(REMOVED_TAGS):
            tag.decompose()

        title = _collapse(soup.title.get_text(" ", strip=True)) if soup.title else ""
        if not title:
            title = _meta_content(soup, "property", "og:title")
        title = _truncate(title, 300)

        meta_description = _meta_content(soup, "name", "description")
        if not meta_description:
            meta_description = _meta_content(soup, "property", "og:description")
        meta_description = _truncate(meta_description, 500)

        headings: list[str] = []
        for heading in soup.select("h1, h2, h3"):
            text = _truncate(_collapse(heading.get_text(" ", strip=True)), 200)
            if text and text not in headings:
                headings.append(text)
            if len(headings) == 30:
                break

        container = _container(soup)
        text = _collapse(container.get_text(" ", strip=True)) if container else ""
        text = _truncate(text, self._max_chars)

        return WebsiteContent(
            url=url,
            final_url=final_url,
            title=title,
            meta_description=meta_description,
            headings=headings,
            text=text,
            fetched_at=datetime.now(UTC),
        )
