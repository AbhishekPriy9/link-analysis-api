import ipaddress
import socket

import pytest

from app.utils.validators import (
    HostResolutionError,
    InvalidUrlError,
    assert_public_url,
    is_public_ip,
    normalize_url,
)


def test_normalize_url_lowercases_host_and_drops_fragment() -> None:
    assert (
        normalize_url("  HTTPS://Example.COM:8443/Path?A=1#section ")
        == "https://example.com:8443/Path?A=1"
    )


def test_normalize_url_keeps_ipv6_host() -> None:
    assert normalize_url("http://[::1]:8080/x") == "http://[::1]:8080/x"


def test_normalize_url_drops_the_default_port() -> None:
    assert normalize_url("https://example.com:443/Path") == "https://example.com/Path"
    assert normalize_url("http://example.com:80/") == "http://example.com/"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "example.com",
        "ftp://example.com",
        "https://",
        "http://[::1",
    ],
)
def test_normalize_url_rejects_missing_or_unsupported_parts(raw: str) -> None:
    with pytest.raises(InvalidUrlError):
        normalize_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://user@example.com/",
        "https://user:secret@example.com/",
        "http://example.com:/path",
        "http://example.com:abc/path",
        "http://example.com:99999/path",
        "http://example.com:0/path",
    ],
)
def test_normalize_url_rejects_credentials_and_bad_ports(raw: str) -> None:
    with pytest.raises(InvalidUrlError):
        normalize_url(raw)


def test_is_public_ip_rejects_non_public_ranges() -> None:
    assert is_public_ip(ipaddress.ip_address("93.184.216.34"))
    for address in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "224.0.0.1", "0.0.0.0"):
        assert not is_public_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://app.localhost/",
        "http://printer.local/",
        "http://service.internal/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
async def test_assert_public_url_rejects_internal_hostnames(url: str) -> None:
    with pytest.raises(InvalidUrlError):
        await assert_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://192.0.2.10/",
    ],
)
async def test_assert_public_url_rejects_non_public_ip_literals(url: str) -> None:
    with pytest.raises(InvalidUrlError):
        await assert_public_url(url)


async def test_assert_public_url_rejects_malformed_ipv6() -> None:
    with pytest.raises(InvalidUrlError):
        await assert_public_url("http://[::1")


async def test_assert_public_url_accepts_public_resolution(monkeypatch) -> None:
    calls = []

    def fake_getaddrinfo(host: str, port: int, **kwargs):
        calls.append((host, port, kwargs))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    resolved = await assert_public_url("https://example.com/about")

    assert resolved == ["93.184.216.34"]
    assert calls == [("example.com", 443, {"type": socket.SOCK_STREAM})]


async def test_assert_public_url_rejects_mixed_resolution(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, port: int, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(InvalidUrlError):
        await assert_public_url("https://example.com/")


async def test_assert_public_url_rejects_dns_failure(monkeypatch) -> None:
    def failing_getaddrinfo(host: str, port: int, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)

    with pytest.raises(HostResolutionError):
        await assert_public_url("https://does-not-resolve.example/")
