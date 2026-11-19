import ipaddress
import socket
from functools import partial
from urllib.parse import urlparse, urlunsplit

import anyio

DEFAULT_PORTS = {"http": 80, "https": 443}
BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")


class InvalidUrlError(ValueError):
    pass


class HostResolutionError(Exception):
    pass


def normalize_url(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        raise InvalidUrlError("A URL is required.")

    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
    except ValueError as exc:
        raise InvalidUrlError("The URL is not valid.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidUrlError("Only http and https URLs are supported.")
    if not hostname:
        raise InvalidUrlError("The URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidUrlError("URLs with credentials are not allowed.")
    if parsed.netloc.endswith(":"):
        raise InvalidUrlError("The URL contains an empty port.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidUrlError("The URL contains an invalid port.") from exc
    if port == 0:
        raise InvalidUrlError("The URL contains an invalid port.")
    if port == DEFAULT_PORTS[scheme]:
        port = None

    host = hostname.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"

    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    blocked = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
    return ip.is_global and not blocked


async def assert_public_url(url: str) -> list[str]:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise InvalidUrlError("The URL is not valid.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidUrlError("Only http and https URLs are supported.")
    if not hostname:
        raise InvalidUrlError("The URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidUrlError("URLs with credentials are not allowed.")
    if parsed.netloc.endswith(":"):
        raise InvalidUrlError("The URL contains an empty port.")

    host = hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise InvalidUrlError("The URL points to a private or internal host.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidUrlError("The URL contains an invalid port.") from exc
    if port is None:
        port = DEFAULT_PORTS[scheme]

    resolver = partial(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    try:
        infos = await anyio.to_thread.run_sync(resolver, abandon_on_cancel=True)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        raise HostResolutionError("The hostname could not be resolved.") from exc

    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError as exc:
            raise InvalidUrlError(
                "The hostname resolved to an invalid address."
            ) from exc

    if not addresses:
        raise HostResolutionError("The hostname did not resolve to any address.")
    if any(not is_public_ip(address) for address in addresses):
        raise InvalidUrlError("The URL resolves to a private or internal address.")

    ordered = sorted(addresses, key=lambda address: address.version)
    return list(dict.fromkeys(address.compressed for address in ordered))
