#!/usr/bin/env python3
"""
Regression tests for tools/_safe_paths.py (H-1 fix).

Covers the cases an attacker-controlled wiki page_spec['path'] could carry:
  - absolute paths
  - ..  traversal
  - empty / None / non-string input
  - symlink escapes
  - valid wiki-relative paths

Run from the repo root:
  python3 tests/test_safe_paths.py
Exit 0 = all pass, 1 = any fail.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from _safe_paths import safe_wiki_path, safe_wiki_write, UnsafePathError  # noqa: E402


results = {"pass": 0, "fail": 0}


def _ok(label: str) -> None:
    results["pass"] += 1
    print(f"  ✓ {label}")


def _bad(label: str) -> None:
    results["fail"] += 1
    print(f"  ✗ {label}")


def expect_ok(label: str, cwd: Path, rel: str) -> None:
    try:
        got = safe_wiki_path(cwd, rel)
    except Exception as exc:
        _bad(f"{label} — raised {exc!r}")
        return
    wiki_root = (cwd / "wiki").resolve()
    try:
        got.relative_to(wiki_root)
    except ValueError:
        _bad(f"{label} — resolved outside wiki: {got}")
        return
    _ok(f"{label} → {got.relative_to(wiki_root)}")


def expect_reject(label: str, cwd: Path, rel) -> None:
    try:
        got = safe_wiki_path(cwd, rel)
    except UnsafePathError as exc:
        _ok(f"{label} — rejected: {exc}")
        return
    _bad(f"{label} — accepted {rel!r} → {got}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        # Resolve so macOS /var → /private/var doesn't confuse display logic.
        cwd = Path(tmp).resolve()
        (cwd / "wiki").mkdir()

        # — Accepts —
        expect_ok("plain relative under wiki/", cwd, "wiki/entities/Foo.md")
        expect_ok("nested but still under wiki/", cwd, "wiki/decisions/2026/q2/d.md")

        # — Rejects —
        expect_reject("absolute path", cwd, "/etc/passwd")
        expect_reject("absolute path via ~",
                      cwd, str(Path.home() / ".claude" / "hooks" / "pith" / "post-tool-use.js"))
        expect_reject("traversal via ..", cwd, "wiki/../../../tmp/pwn.md")
        expect_reject("traversal to sibling of cwd", cwd, "../sibling/pwn.md")
        expect_reject("path outside wiki/ (sibling dir)", cwd, "raw/sources/pwn.md")
        expect_reject("empty string", cwd, "")
        expect_reject("whitespace only", cwd, "   ")
        expect_reject("None", cwd, None)
        expect_reject("non-string (int)", cwd, 42)

        # — Symlink escape — a directory symlink inside wiki/ that points
        # outside the wiki root must cause rejection.
        outside = cwd.parent / (cwd.name + "_outside")
        outside.mkdir(exist_ok=True)
        link = cwd / "wiki" / "escape"
        try:
            link.symlink_to(outside)
            expect_reject("symlink inside wiki/ escaping out",
                          cwd, "wiki/escape/pwn.md")
        finally:
            if link.is_symlink():
                link.unlink()
            try:
                outside.rmdir()
            except OSError:
                pass

        # — safe_wiki_write round-trip —
        try:
            target = safe_wiki_write(cwd, "wiki/entities/Bar.md", "hello")
            if target.read_text() == "hello":
                _ok("safe_wiki_write writes under wiki/")
            else:
                _bad("safe_wiki_write produced wrong content")
        except Exception as exc:
            _bad(f"safe_wiki_write happy-path raised {exc!r}")

    print()
    print(f"── Results ── passed: {results['pass']}  failed: {results['fail']}")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
