#!/usr/bin/env python3
"""
Pith Wiki Query — find and return relevant wiki pages for a question.

Search priority:
  1. GrepAI semantic search (if installed + indexed)
  2. Keyword scoring fallback (always available)

If GrepAI is not installed a one-time nudge is shown each session until
the user runs /pith grepai skip to permanently dismiss it.

Usage:
    python3 wiki.py --question "why did we choose postgres?"
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── State helpers (mirrors config.js logic) ──────────────────────────────────

STATE_PATH = Path.home() / '.pith' / 'state.json'


def _project_key() -> str:
    cwd = (os.environ.get('CLAUDE_CWD') or os.getcwd()).encode()
    raw = base64.b64encode(cwd).decode()
    return 'proj_' + re.sub(r'[^a-zA-Z0-9]', '', raw)[:20]


def _load_proj_state() -> dict:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text())
            return data.get(_project_key(), {})
    except Exception:
        pass
    return {}


def _save_proj_state(updates: dict) -> None:
    try:
        data = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
        key = _project_key()
        data[key] = {**data.get(key, {}), **updates}
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


# ── Keyword fallback (original scorer) ───────────────────────────────────────

STOP_WORDS = {
    'the','a','an','is','are','was','were','be','have','do','how','what',
    'where','when','why','who','which','that','this','to','of','in','for',
    'on','with','at','by','from','and','but','or','not','if','about',
}


def keywords(text: str) -> set[str]:
    words = re.findall(r'[a-zA-Z][a-zA-Z0-9]*', text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) > 2}


def score_page(text: str, kws: set[str]) -> float:
    if not kws:
        return 0.0
    low = text.lower()
    hits = sum(1 + min(low.count(kw) - 1, 2) * 0.3 for kw in kws if low.count(kw) > 0)
    return hits / (1 + len(low.split()) / 200)


def keyword_search(pages: list[Path], question: str, top_k: int) -> list[tuple[float, Path, str]]:
    """Score pages by keyword overlap and return top_k results."""
    kws = keywords(question)
    scored = []
    for page_path in pages:
        if not page_path.exists():
            continue
        content = page_path.read_text(errors='ignore')
        s = score_page(content, kws)
        if 'why' in question.lower() and 'decisions' in str(page_path):
            s *= 1.5
        scored.append((s, page_path, content))
    scored.sort(key=lambda x: -x[0])
    return [x for x in scored[:top_k] if x[0] > 0]


# ── GrepAI semantic search ────────────────────────────────────────────────────

def grepai_available() -> bool:
    return shutil.which('grepai') is not None


def grepai_indexed(wiki_dir: Path) -> bool:
    """Check whether the wiki has been indexed by GrepAI."""
    # GrepAI stores its index in .grepai/ inside the searched directory
    return (wiki_dir / '.grepai').exists() or (wiki_dir / '.grepai-index').exists()


def grepai_index(wiki_dir: Path) -> bool:
    """Build (or refresh) the GrepAI index for wiki_dir. Returns True on success."""
    try:
        result = subprocess.run(
            ['grepai', 'index', str(wiki_dir)],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def grepai_search(question: str, wiki_dir: Path, top_k: int) -> list[tuple[float, Path, str]] | None:
    """
    Run GrepAI semantic search. Returns list of (score, path, content) or
    None if GrepAI fails (caller falls back to keyword search).
    """
    try:
        result = subprocess.run(
            ['grepai', 'search', question,
             '--dir', str(wiki_dir),
             '--top', str(top_k),
             '--format', 'json'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        data = json.loads(result.stdout)
        # GrepAI JSON output: list of {file, score, snippet} or {path, score, content}
        hits = []
        for item in data[:top_k]:
            file_key  = item.get('file') or item.get('path') or ''
            score     = float(item.get('score', 0.5))
            snippet   = item.get('snippet') or item.get('content') or ''
            page_path = Path(file_key) if Path(file_key).is_absolute() else wiki_dir / file_key
            if page_path.exists():
                content = page_path.read_text(errors='ignore')
            else:
                content = snippet
            hits.append((score, page_path, content))
        return hits if hits else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


# ── Nudge logic ───────────────────────────────────────────────────────────────

NUDGE = (
    '\n[PITH: GrepAI not installed — wiki is using keyword search (lower accuracy on '
    'semantic queries like "why did we choose X").\n'
    '  Install: https://github.com/yoanbernabeu/grepai\n'
    '  Dismiss this notice: /pith grepai skip\n'
    '  Re-enable later:     /pith grepai enable]\n'
)


def maybe_nudge() -> str:
    """Return nudge string if GrepAI is absent and user hasn't dismissed it."""
    state = _load_proj_state()
    if state.get('grepai_skip'):
        return ''
    # Show once per session (track via session_start timestamp)
    session = state.get('session_start', '')
    if state.get('grepai_nudge_session') == session:
        return ''
    # Mark as shown for this session
    _save_proj_state({'grepai_nudge_session': session})
    return NUDGE


# ── Wiki discovery ────────────────────────────────────────────────────────────

def find_wiki(cwd: Path) -> Path | None:
    for candidate in [cwd / 'wiki', cwd / 'docs' / 'wiki', cwd / '.wiki']:
        if candidate.is_dir():
            return candidate
    return None


def parse_index(index_path: Path) -> list[tuple[str, Path]]:
    if not index_path.exists():
        return []
    content = index_path.read_text(errors='ignore')
    entries = []
    for line in content.split('\n'):
        m = re.match(r'\s*-\s+\[\[([^\]]+)\]\]\(([^)]+)\)', line)
        if m:
            entries.append((m.group(1), index_path.parent / m.group(2)))
    return entries


# ── Main query ────────────────────────────────────────────────────────────────

def query(question: str, top_k: int = 4) -> str:
    cwd  = Path(os.environ.get('CLAUDE_CWD') or os.getcwd())
    wiki = find_wiki(cwd)

    if not wiki:
        return '[PITH WIKI: no wiki directory found. Run /pith setup to create one.]'

    index_path = wiki / 'index.md'
    if index_path.exists():
        pages = [p for _, p in parse_index(index_path) if p.exists()]
    else:
        pages = list(wiki.rglob('*.md'))

    if not pages:
        return '[PITH WIKI: wiki is empty. Use /pith ingest <file> to add sources.]'

    nudge  = ''
    method = 'keyword'
    top: list[tuple[float, Path, str]] = []

    # ── Try GrepAI first ──────────────────────────────────────────────────────
    if grepai_available():
        if not grepai_indexed(wiki):
            grepai_index(wiki)          # first-time index, silent failure ok
        hits = grepai_search(question, wiki, top_k)
        if hits:
            top    = hits
            method = 'semantic'
    else:
        nudge = maybe_nudge()

    # ── Keyword fallback ──────────────────────────────────────────────────────
    if not top:
        top = keyword_search(pages, question, top_k)

    if not top:
        return (
            f'[PITH WIKI: no relevant pages found for "{question[:60]}". '
            f'Wiki has {len(pages)} pages total.]{nudge}'
        )

    tag   = 'SEMANTIC' if method == 'semantic' else 'KEYWORD'
    parts = [
        f'[PITH WIKI ({tag}): {len(pages)} pages · '
        f'{len(top)} relevant to: "{question[:80]}"]\n'
    ]
    for _, page_path, content in top:
        try:
            rel = page_path.relative_to(cwd)
        except ValueError:
            rel = page_path
        lines   = content.split('\n')
        preview = '\n'.join(lines[:40])
        if len(lines) > 40:
            preview += f'\n[...{len(lines) - 40} more lines — ask for full page: {rel}]'
        parts.append(f'--- {rel} ---\n{preview}')

    return '\n\n'.join(parts) + nudge


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--question', '-q', default='')
    p.add_argument('--top', '-k', type=int, default=4)
    args = p.parse_args()
    print(query(args.question or ' '.join(sys.argv[1:]), args.top))


if __name__ == '__main__':
    main()
