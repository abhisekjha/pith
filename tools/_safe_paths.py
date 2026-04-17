"""
Shared path-safety helpers for Pith tools.

The `ingest` and `compile` workflows ask an LLM to propose wiki page paths.
The LLM's input is untrusted (external documents, fetched URLs), so its
output — and therefore the proposed paths — must be treated as untrusted too.

Without validation, `Path(cwd) / proposed` accepts absolute paths verbatim
(`Path('/a') / '/b' == Path('/b')`) and `..` traversal, which would let a
poisoned source overwrite arbitrary files — including the hook scripts
themselves at ~/.claude/hooks/pith/, turning a write into persistent RCE.

`safe_wiki_path` resolves the candidate under `<cwd>/wiki/`, refuses symlinks
whose targets escape, and raises on anything outside. Callers should catch
`ValueError` and skip the offending page without aborting the whole run.
"""
from __future__ import annotations
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a proposed wiki path escapes the wiki root."""


def safe_wiki_path(cwd: Path, rel: object, wiki_subdir: str = "wiki") -> Path:
    """Return an absolute path guaranteed to live under <cwd>/<wiki_subdir>/.

    Refuses: empty/None, non-string input, absolute paths, paths that
    resolve outside the wiki root (via `..` or symlinks), and any
    candidate whose own node is a symlink pointing outside.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise UnsafePathError(f"page path must be a non-empty string, got {rel!r}")

    wiki_root = (cwd / wiki_subdir).resolve()
    # Reject absolute paths outright — even if they happen to point inside the
    # wiki, accepting them would set a bad precedent and complicates review.
    if Path(rel).is_absolute():
        raise UnsafePathError(f"page path must be relative to cwd: {rel!r}")

    candidate = (cwd / rel).resolve()

    # If any ancestor is a symlink whose target escapes the wiki, reject.
    try:
        candidate.relative_to(wiki_root)
    except ValueError as exc:
        raise UnsafePathError(f"page path escapes {wiki_subdir}/: {rel!r}") from exc

    # `.resolve()` above already follows symlinks, so intermediate directory
    # symlinks that escape the wiki root are already caught by relative_to().
    # This check covers the narrow case where `candidate` itself exists as a
    # symlink at the resolved path (e.g. a wiki-internal symlink added manually)
    # — we refuse to overwrite through it even if the target is inside the wiki.
    if candidate.is_symlink():
        raise UnsafePathError(f"refusing to write through a symlink: {rel!r}")

    return candidate


def safe_wiki_write(cwd: Path, rel: object, content: str,
                    wiki_subdir: str = "wiki") -> Path:
    """Convenience: resolve with safe_wiki_path then write_text.

    Returns the written path. Creates parent directories. Raises
    `UnsafePathError` for any rejected path; the caller should catch
    it, log, and continue with the next page.
    """
    target = safe_wiki_path(cwd, rel, wiki_subdir=wiki_subdir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target
