#!/usr/bin/env python3
"""
Regression tests for tools/_safe_fetch.py (M-4 fix).

Exercises each branch of the policy without touching the network. The
happy-path test patches `_is_public_host` + `urlopen` to avoid a real DNS
lookup + outbound connection.
"""
from __future__ import annotations
import sys
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from _safe_fetch import safe_fetch, UnsafeFetchError  # noqa: E402


results = {"pass": 0, "fail": 0}


def _ok(label: str) -> None:
    results["pass"] += 1
    print(f"  ✓ {label}")


def _bad(label: str) -> None:
    results["fail"] += 1
    print(f"  ✗ {label}")


def expect_reject(label: str, url: str, substring: str = "") -> None:
    try:
        safe_fetch(url)
    except UnsafeFetchError as exc:
        if substring and substring not in str(exc):
            _bad(f"{label} — raised UnsafeFetchError but message missing {substring!r}: {exc}")
            return
        _ok(f"{label} — rejected: {exc}")
        return
    except Exception as exc:
        _bad(f"{label} — raised wrong error: {exc!r}")
        return
    _bad(f"{label} — fetch was NOT rejected")


def run() -> int:
    # — Scheme rejects —
    expect_reject("file:// scheme", "file:///etc/passwd", "scheme not allowed")
    expect_reject("ftp:// scheme", "ftp://example.com/x", "scheme not allowed")
    expect_reject("gopher:// scheme", "gopher://example.com/", "scheme not allowed")
    expect_reject("bare path with no scheme", "/etc/passwd", "scheme not allowed")
    expect_reject("javascript: scheme", "javascript:alert(1)", "scheme not allowed")

    # — No hostname —
    expect_reject("empty URL", "", "non-empty")
    expect_reject("http:// with no host", "http://", "hostname")

    # — Private / loopback / link-local hosts (resolution is trivial for raw IPs) —
    expect_reject("loopback IPv4", "http://127.0.0.1/x", "non-public")
    expect_reject("loopback IPv6", "http://[::1]/x", "non-public")
    expect_reject("RFC1918 10/8", "http://10.0.0.5/x", "non-public")
    expect_reject("RFC1918 192.168", "http://192.168.1.1/x", "non-public")
    expect_reject("link-local (AWS IMDS)", "http://169.254.169.254/latest/meta-data/", "non-public")
    expect_reject("link-local IPv6", "http://[fe80::1]/x", "non-public")

    # — Happy path: scheme ok, host resolves to a public IP, small body —
    fake_resp = mock.Mock()
    fake_resp.read.return_value = b"hello world"
    fake_resp.headers = {"Content-Type": "text/plain"}
    fake_resp.status = 200
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None

    with mock.patch("_safe_fetch._is_public_host", return_value=True), \
         mock.patch("_safe_fetch.urllib.request.urlopen", return_value=fake_resp):
        try:
            body, ct = safe_fetch("https://example.com/x")
            if body == b"hello world" and ct == "text/plain":
                _ok("happy-path fetch (mocked) returns (body, content_type)")
            else:
                _bad(f"happy-path wrong result: body={body!r} ct={ct!r}")
        except Exception as exc:
            _bad(f"happy-path raised {exc!r}")

    # — Size cap —
    oversized = mock.Mock()
    oversized.read.return_value = b"x" * (5 * 1024 * 1024 + 2)
    oversized.headers = {"Content-Type": "text/plain"}
    oversized.status = 200
    oversized.__enter__ = lambda self: self
    oversized.__exit__ = lambda self, *a: None

    with mock.patch("_safe_fetch._is_public_host", return_value=True), \
         mock.patch("_safe_fetch.urllib.request.urlopen", return_value=oversized):
        try:
            safe_fetch("https://example.com/big")
            _bad("oversized response was NOT rejected")
        except UnsafeFetchError as exc:
            if "limit" in str(exc):
                _ok(f"oversized response rejected: {exc}")
            else:
                _bad(f"oversized response raised unexpected msg: {exc}")
        except Exception as exc:
            _bad(f"oversized response raised wrong error: {exc!r}")

    # — PITH_INGEST_ALLOW_PRIVATE override —
    import os
    old = os.environ.get("PITH_INGEST_ALLOW_PRIVATE")
    os.environ["PITH_INGEST_ALLOW_PRIVATE"] = "1"
    try:
        with mock.patch("_safe_fetch.urllib.request.urlopen", return_value=fake_resp):
            try:
                body, _ct = safe_fetch("http://10.0.0.5/internal")
                if body == b"hello world":
                    _ok("PITH_INGEST_ALLOW_PRIVATE=1 permits RFC1918 host")
                else:
                    _bad("env override yielded wrong body")
            except UnsafeFetchError as exc:
                _bad(f"env override still rejected: {exc}")
    finally:
        if old is None:
            del os.environ["PITH_INGEST_ALLOW_PRIVATE"]
        else:
            os.environ["PITH_INGEST_ALLOW_PRIVATE"] = old

    print()
    print(f"── Results ── passed: {results['pass']}  failed: {results['fail']}")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
