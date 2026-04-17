#!/usr/bin/env python3
"""
Regression tests for tools/_safe_fetch.py.

Covers:
  v1 fixes  — scheme rejects, private-IP rejects, size cap, allow_private
  v2 fixes  — DNS pinning (no second lookup), redirect-to-private blocked,
               redirect loop limit, valid redirect followed
"""
from __future__ import annotations
import os
import sys
import unittest.mock as mock
import urllib.error
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from _safe_fetch import safe_fetch, UnsafeFetchError, _build_pinned_opener  # noqa: E402

results = {"pass": 0, "fail": 0}


def _ok(label: str) -> None:
    results["pass"] += 1
    print(f"  ✓ {label}")


def _bad(label: str) -> None:
    results["fail"] += 1
    print(f"  ✗ {label}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_resp(body: bytes = b"hello world", ct: str = "text/plain", status: int = 200):
    """Mock HTTP response context manager."""
    r = mock.Mock()
    r.read.return_value = body
    r.headers = {"Content-Type": ct}
    r.status = status
    r.__enter__ = lambda s: s
    r.__exit__ = lambda s, *a: None
    return r


def _fake_opener(resp):
    """Mock OpenerDirector whose open() returns resp."""
    opener = mock.Mock()
    opener.open.return_value = resp
    return opener


def _redirect_opener(location: str, code: int = 302):
    """Mock opener whose open() raises HTTPError(code) with a Location header."""
    hdrs = Message()
    hdrs["Location"] = location
    opener = mock.Mock()
    opener.open.side_effect = urllib.error.HTTPError(
        "https://example.com/", code, "Redirect", hdrs, None
    )
    return opener


def expect_reject(label: str, url: str, substring: str = "") -> None:
    try:
        safe_fetch(url)
    except UnsafeFetchError as exc:
        if substring and substring not in str(exc):
            _bad(f"{label} — wrong message (missing {substring!r}): {exc}")
            return
        _ok(f"{label} — rejected: {exc}")
    except Exception as exc:
        _bad(f"{label} — raised wrong error type: {exc!r}")
    else:
        _bad(f"{label} — fetch was NOT rejected")


# ── Test suite ────────────────────────────────────────────────────────────────

def run() -> int:

    # ── Scheme rejects (no network needed) ────────────────────────────────────
    expect_reject("file:// scheme",              "file:///etc/passwd",            "scheme not allowed")
    expect_reject("ftp:// scheme",               "ftp://example.com/x",           "scheme not allowed")
    expect_reject("gopher:// scheme",            "gopher://example.com/",         "scheme not allowed")
    expect_reject("bare path / no scheme",       "/etc/passwd",                   "scheme not allowed")
    expect_reject("javascript: scheme",          "javascript:alert(1)",           "scheme not allowed")

    # ── No hostname ────────────────────────────────────────────────────────────
    expect_reject("empty URL",                   "",                              "non-empty")
    expect_reject("http:// with no host",        "http://",                       "hostname")

    # ── Private / loopback / link-local IPs (getaddrinfo returns the IP itself)
    expect_reject("loopback IPv4",               "http://127.0.0.1/x",           "non-public")
    expect_reject("loopback IPv6",               "http://[::1]/x",               "non-public")
    expect_reject("RFC1918 10/8",                "http://10.0.0.5/x",            "non-public")
    expect_reject("RFC1918 192.168",             "http://192.168.1.1/x",         "non-public")
    expect_reject("link-local (AWS IMDS)",       "http://169.254.169.254/latest/meta-data/", "non-public")
    expect_reject("link-local IPv6",             "http://[fe80::1]/x",           "non-public")

    # ── Happy path (mocked DNS + pinned opener) ────────────────────────────────
    resp = _fake_resp()
    with mock.patch("_safe_fetch._resolve_to_public_ip", return_value="1.2.3.4"), \
         mock.patch("_safe_fetch._build_pinned_opener", return_value=_fake_opener(resp)):
        try:
            body, ct = safe_fetch("https://example.com/x")
            if body == b"hello world" and ct == "text/plain":
                _ok("happy-path returns (body, content_type)")
            else:
                _bad(f"happy-path wrong result: {body!r} {ct!r}")
        except Exception as exc:
            _bad(f"happy-path raised: {exc!r}")

    # ── Size cap ───────────────────────────────────────────────────────────────
    big = _fake_resp(body=b"x" * (5 * 1024 * 1024 + 2))
    with mock.patch("_safe_fetch._resolve_to_public_ip", return_value="1.2.3.4"), \
         mock.patch("_safe_fetch._build_pinned_opener", return_value=_fake_opener(big)):
        try:
            safe_fetch("https://example.com/big")
            _bad("oversized response was NOT rejected")
        except UnsafeFetchError as exc:
            if "limit" in str(exc):
                _ok(f"oversized response rejected: {exc}")
            else:
                _bad(f"oversized response wrong message: {exc}")
        except Exception as exc:
            _bad(f"oversized response wrong error type: {exc!r}")

    # ── PITH_INGEST_ALLOW_PRIVATE=1 bypasses IP check ─────────────────────────
    old = os.environ.get("PITH_INGEST_ALLOW_PRIVATE")
    os.environ["PITH_INGEST_ALLOW_PRIVATE"] = "1"
    try:
        private_opener = _fake_opener(_fake_resp())
        with mock.patch("urllib.request.build_opener", return_value=private_opener):
            try:
                body, _ = safe_fetch("http://10.0.0.5/internal")
                if body == b"hello world":
                    _ok("PITH_INGEST_ALLOW_PRIVATE=1 permits RFC1918 host")
                else:
                    _bad("allow_private yielded wrong body")
            except UnsafeFetchError as exc:
                _bad(f"allow_private still rejected: {exc}")
    finally:
        if old is None:
            os.environ.pop("PITH_INGEST_ALLOW_PRIVATE", None)
        else:
            os.environ["PITH_INGEST_ALLOW_PRIVATE"] = old

    # ── PITH_INGEST_ALLOW_PRIVATE=1 still blocks file:// ──────────────────────
    old = os.environ.get("PITH_INGEST_ALLOW_PRIVATE")
    os.environ["PITH_INGEST_ALLOW_PRIVATE"] = "1"
    try:
        expect_reject("file:// blocked even with allow_private", "file:///etc/passwd", "scheme not allowed")
    finally:
        if old is None:
            os.environ.pop("PITH_INGEST_ALLOW_PRIVATE", None)
        else:
            os.environ["PITH_INGEST_ALLOW_PRIVATE"] = old

    # ── DNS pinning: _build_pinned_opener called with resolved IP ─────────────
    # Verify safe_fetch passes the resolved IP to _build_pinned_opener so the
    # TCP socket is pinned — no second DNS lookup possible.
    resp = _fake_resp()
    with mock.patch("_safe_fetch._resolve_to_public_ip", return_value="93.184.216.34") as mock_resolve, \
         mock.patch("_safe_fetch._build_pinned_opener", return_value=_fake_opener(resp)) as mock_pin:
        try:
            safe_fetch("https://example.com/page")
            # Check that _build_pinned_opener received the resolved IP
            call_args = mock_pin.call_args
            if call_args and call_args[0][1] == "93.184.216.34":
                _ok("DNS pinning: _build_pinned_opener received the pre-resolved IP")
            else:
                _bad(f"DNS pinning: wrong IP passed to opener builder: {call_args}")
        except Exception as exc:
            _bad(f"DNS pinning test raised: {exc!r}")

    # ── Redirect to private IP blocked (SSRF-via-redirect fix) ────────────────
    # Public host → 302 → http://10.0.0.1/ must be rejected.
    # Mock _resolve_to_public_ip only for the initial public host; for the
    # redirect target (10.0.0.1) the real implementation runs and raises.
    import _safe_fetch as _sf_mod
    _real_resolve = _sf_mod._resolve_to_public_ip

    def _resolve_public_only(host):
        if host == "example.com":
            return "1.2.3.4"
        return _real_resolve(host)   # real check — rejects 10.0.0.1

    with mock.patch("_safe_fetch._resolve_to_public_ip", side_effect=_resolve_public_only), \
         mock.patch("_safe_fetch._build_pinned_opener",
                    return_value=_redirect_opener("http://10.0.0.1/secret")):
        try:
            safe_fetch("https://example.com/redir")
            _bad("redirect to private IP was NOT rejected")
        except UnsafeFetchError as exc:
            if "non-public" in str(exc):
                _ok(f"redirect to private IP blocked: {exc}")
            else:
                _bad(f"redirect blocked but wrong reason: {exc}")
        except Exception as exc:
            _bad(f"redirect-to-private raised wrong error: {exc!r}")

    # ── Redirect to file:// blocked ────────────────────────────────────────────
    with mock.patch("_safe_fetch._resolve_to_public_ip", return_value="1.2.3.4"), \
         mock.patch("_safe_fetch._build_pinned_opener",
                    return_value=_redirect_opener("file:///etc/passwd", code=301)):
        try:
            safe_fetch("https://example.com/redir")
            _bad("redirect to file:// was NOT rejected")
        except UnsafeFetchError as exc:
            if "scheme" in str(exc):
                _ok(f"redirect to file:// blocked: {exc}")
            else:
                _bad(f"redirect to file:// blocked but wrong reason: {exc}")
        except Exception as exc:
            _bad(f"redirect-to-file raised wrong error: {exc!r}")

    # ── Redirect loop limit (> 5 hops) ────────────────────────────────────────
    # Every hop redirects to the same public URL — must fail after 5.
    with mock.patch("_safe_fetch._resolve_to_public_ip", return_value="1.2.3.4"), \
         mock.patch("_safe_fetch._build_pinned_opener",
                    return_value=_redirect_opener("https://example.com/loop")):
        try:
            safe_fetch("https://example.com/loop")
            _bad("infinite redirect loop was NOT rejected")
        except UnsafeFetchError as exc:
            if "redirect" in str(exc):
                _ok(f"redirect loop rejected after limit: {exc}")
            else:
                _bad(f"loop stopped but wrong reason: {exc}")
        except Exception as exc:
            _bad(f"redirect loop raised wrong error: {exc!r}")

    # ── Valid redirect followed (public → public) ──────────────────────────────
    # First opener → 302 to https://other.example.com/
    # Second call → valid response.
    final_resp = _fake_resp(body=b"redirected content")
    call_count = {"n": 0}

    def patched_resolve(host):
        return "1.2.3.4"

    def patched_opener(host, ip, scheme):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _redirect_opener("https://other.example.com/final")
        return _fake_opener(final_resp)

    with mock.patch("_safe_fetch._resolve_to_public_ip", side_effect=patched_resolve), \
         mock.patch("_safe_fetch._build_pinned_opener", side_effect=patched_opener):
        try:
            body, _ = safe_fetch("https://example.com/start")
            if body == b"redirected content":
                _ok("valid redirect followed (public → public)")
            else:
                _bad(f"valid redirect yielded wrong body: {body!r}")
        except Exception as exc:
            _bad(f"valid redirect raised: {exc!r}")

    print()
    print(f"── Results ── passed: {results['pass']}  failed: {results['fail']}")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
