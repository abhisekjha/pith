"""
SSRF-hardened URL fetcher for `ingest --url`.

The original `urllib.request.urlopen` accepts `file://`, `ftp://`, `gopher://`
and any host — including loopback and link-local addresses — which meant a
crafted `/pith ingest --url` could read local files or hit the cloud-metadata
endpoint (169.254.169.254). On top of that, the fetched body flows into a
Claude prompt and then into `write_text` calls, so SSRF composes with
indirect prompt injection.

`safe_fetch` enforces:
  - scheme ∈ {http, https}
  - resolved IP(s) must be public (no loopback / private / link-local /
    reserved / multicast / unspecified)
  - response size ≤ 5 MiB
  - request timeout ≤ 20 s

Set `PITH_INGEST_ALLOW_PRIVATE=1` to intentionally ingest from an internal
URL (the helper still forbids `file://` and other non-HTTP schemes).
"""
from __future__ import annotations
import ipaddress
import os
import socket
import urllib.request
from urllib.parse import urlparse


_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BYTES = 5 * 1024 * 1024
_TIMEOUT = 20
_USER_AGENT = "pith-ingest/1.1"


class UnsafeFetchError(ValueError):
    """Raised when a fetch target fails the safety checks."""


def _is_public_host(host: str) -> bool:
    """True if every resolved IP of `host` is a global/public address.

    Uses getaddrinfo so IPv4 and IPv6 results are both evaluated. If resolution
    fails we return False — better to refuse than to silently try.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        # info[4] is sockaddr — (host, port) for AF_INET, (host, port, flow, scope)
        # for AF_INET6. host is always a string.
        sockaddr = info[4]
        ip_str = str(sockaddr[0]) if sockaddr else ""
        # Strip IPv6 zone id if present (fe80::1%en0).
        ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def safe_fetch(url: str) -> tuple[bytes, str]:
    """Fetch `url` and return (body, content_type).

    Raises `UnsafeFetchError` if the URL fails policy checks, and
    `urllib.error.URLError` for transport failures (callers already handle
    this).
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeFetchError("URL must be a non-empty string")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeFetchError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeFetchError("URL has no hostname")

    allow_private = os.environ.get("PITH_INGEST_ALLOW_PRIVATE") == "1"
    if not allow_private and not _is_public_host(host):
        raise UnsafeFetchError(
            f"refusing non-public host: {host!r} "
            "(set PITH_INGEST_ALLOW_PRIVATE=1 to override)"
        )

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        status = getattr(resp, "status", 200)
        if status and status >= 400:
            raise UnsafeFetchError(f"HTTP {status}")
        body = resp.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise UnsafeFetchError(
                f"response exceeds {_MAX_BYTES // (1024 * 1024)} MiB limit"
            )
        return body, resp.headers.get("Content-Type", "")
