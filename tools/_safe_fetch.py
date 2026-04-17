"""
SSRF-hardened URL fetcher for `ingest --url`.

v2 — closes two residual risks from v1:

  DNS rebinding (was residual):
    v1 resolved the hostname to validate the IP, then let urllib open a fresh
    connection — which does its own DNS lookup. A hostile DNS could return a
    public IP for the check and 127.0.0.1 for the connect.
    v2 resolves once, then pins the TCP socket to that IP via a custom
    connection subclass. No second lookup ever happens.

  SSRF via HTTP redirect (was not handled):
    v1 let urllib auto-follow redirects without re-checking the target IP.
    A public server that responds 302 → http://10.0.0.1/ bypassed all checks.
    v2 disables auto-follow and validates every redirect hop manually before
    recursing into safe_fetch (limit: 5 hops).

Set PITH_INGEST_ALLOW_PRIVATE=1 to bypass IP checks (http/https still enforced).
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BYTES       = 5 * 1024 * 1024
_TIMEOUT         = 20
_MAX_REDIRECTS   = 5
_USER_AGENT      = "pith-ingest/1.1"
_REDIRECT_CODES  = frozenset({301, 302, 303, 307, 308})


class UnsafeFetchError(ValueError):
    """Raised when a fetch target fails the safety checks."""


# ── IP validation ─────────────────────────────────────────────────────────────

def _check_ip(ip_str: str, host: str) -> None:
    """Raise UnsafeFetchError if ip_str is a non-public address."""
    ip_str = ip_str.split("%", 1)[0]   # strip IPv6 zone id (fe80::1%en0)
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        raise UnsafeFetchError(f"unparseable IP {ip_str!r} for host {host!r}")
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        raise UnsafeFetchError(
            f"refusing non-public host: {host!r} (resolves to {ip_str})"
            " — set PITH_INGEST_ALLOW_PRIVATE=1 to override"
        )


def _resolve_to_public_ip(host: str) -> str:
    """DNS-resolve host, validate every returned IP, return the first one.

    Raises UnsafeFetchError if any address is non-public or resolution fails.
    The returned IP is used to pin the TCP socket so no second lookup occurs.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeFetchError(f"could not resolve {host!r}: {exc}") from exc
    if not infos:
        raise UnsafeFetchError(f"no addresses returned for {host!r}")
    first_ip: str | None = None
    for info in infos:
        ip_str = str(info[4][0])
        _check_ip(ip_str, host)
        if first_ip is None:
            first_ip = ip_str
    assert first_ip is not None
    return first_ip


def _is_public_host(host: str) -> bool:
    """True iff every DNS record for `host` is a public address. Used by tests."""
    try:
        _resolve_to_public_ip(host)
        return True
    except UnsafeFetchError:
        return False


# ── Pinned opener (DNS-rebinding fix) ─────────────────────────────────────────

def _build_pinned_opener(
    host: str, pinned_ip: str, scheme: str
) -> urllib.request.OpenerDirector:
    """Return an opener that:

    1. Connects directly to `pinned_ip` — no second DNS lookup, no rebinding.
       For HTTPS, `host` is still used for cert validation and SNI so TLS works.
    2. Does NOT auto-follow redirects — each hop is validated by safe_fetch
       before recursing, closing the SSRF-via-redirect path.
    """
    _ip = pinned_ip   # capture for closures below

    if scheme == "https":
        class _PinnedConn(http.client.HTTPSConnection):
            def connect(self_c) -> None:                          # type: ignore[override]
                raw = socket.create_connection(
                    (_ip, self_c.port or 443),
                    self_c.timeout,
                    self_c.source_address,
                )
                ctx = self_c._context or ssl.create_default_context()
                # server_hostname=self_c.host keeps cert validation + SNI correct
                self_c.sock = ctx.wrap_socket(raw, server_hostname=self_c.host)

        class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self_h, req):                          # type: ignore[override]
                return self_h.do_open(_PinnedConn, req)

        conn_handler: urllib.request.BaseHandler = _PinnedHTTPSHandler()
    else:
        class _PinnedConn(http.client.HTTPConnection):            # type: ignore[no-redef]
            def connect(self_c) -> None:                          # type: ignore[override]
                self_c.sock = socket.create_connection(
                    (_ip, self_c.port or 80),
                    self_c.timeout,
                    self_c.source_address,
                )

        class _PinnedHTTPHandler(urllib.request.HTTPHandler):
            def http_open(self_h, req):                           # type: ignore[override]
                return self_h.do_open(_PinnedConn, req)

        conn_handler = _PinnedHTTPHandler()

    class _NoAutoRedirect(urllib.request.HTTPRedirectHandler):
        """Returning None from redirect_request causes urllib to raise
        HTTPError(3xx) instead of following the redirect. safe_fetch catches
        that, validates the Location target, then recurses."""
        def redirect_request(self_r, req, fp, code, msg, headers, newurl):
            return None

    return urllib.request.build_opener(conn_handler, _NoAutoRedirect())


# ── Public API ────────────────────────────────────────────────────────────────

def safe_fetch(url: str, *, _hops: int = 0) -> tuple[bytes, str]:
    """Fetch `url` and return (body, content_type).

    Raises:
      UnsafeFetchError      — policy violation (scheme, IP, redirect, size)
      urllib.error.URLError — transport failure (DNS, TLS, timeout, …)
    """
    if _hops > _MAX_REDIRECTS:
        raise UnsafeFetchError(f"too many redirects (limit {_MAX_REDIRECTS})")
    if not isinstance(url, str) or not url.strip():
        raise UnsafeFetchError("URL must be a non-empty string")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeFetchError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeFetchError("URL has no hostname")

    allow_private = os.environ.get("PITH_INGEST_ALLOW_PRIVATE") == "1"
    if allow_private:
        # Scheme check still applies; IP validation and pinning are skipped.
        # Redirect auto-follow is permitted (user has explicitly opted in).
        opener = urllib.request.build_opener()
    else:
        pinned_ip = _resolve_to_public_ip(host)
        opener = _build_pinned_opener(host, pinned_ip, parsed.scheme)

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        resp_ctx = opener.open(req, timeout=_TIMEOUT)
    except urllib.error.HTTPError as exc:
        # _NoAutoRedirect causes urllib to raise HTTPError for 3xx responses.
        # Extract Location, re-validate, recurse — each hop goes through the
        # full safe_fetch pipeline (scheme + IP checks + new DNS pin).
        if exc.code in _REDIRECT_CODES:
            location = (exc.headers.get("Location")
                        or exc.headers.get("location", ""))
            if not location:
                raise UnsafeFetchError(
                    f"HTTP {exc.code} redirect with no Location header"
                ) from exc
            return safe_fetch(location, _hops=_hops + 1)
        raise

    with resp_ctx as resp:
        status = getattr(resp, "status", None)
        if status is not None and status >= 400:
            raise UnsafeFetchError(f"HTTP {status}")
        body = resp.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise UnsafeFetchError(
                f"response exceeds {_MAX_BYTES // (1024 * 1024)} MiB limit"
            )
        return body, resp.headers.get("Content-Type", "")
