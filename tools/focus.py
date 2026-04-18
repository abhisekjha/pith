#!/usr/bin/env python3
"""
Pith Focus — extract relevant sections from a file for the given question.
No LLM call needed: uses keyword matching + structural awareness.

Usage:
    python3 focus.py <filepath> [--question "what you want to know"] [--top 5]
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_FOCUS_CACHE_FILE = Path('/tmp/pith-focus-cache.json')
_FOCUS_CACHE_MAX  = 200

def _load_focus_cache() -> dict:
    try:
        return json.loads(_FOCUS_CACHE_FILE.read_text()) if _FOCUS_CACHE_FILE.exists() else {}
    except Exception:
        return {}

def _save_focus_cache(cache: dict) -> None:
    try:
        if len(cache) > _FOCUS_CACHE_MAX:
            cache = dict(list(cache.items())[-_FOCUS_CACHE_MAX:])
        tmp = _FOCUS_CACHE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(cache))
        tmp.replace(_FOCUS_CACHE_FILE)
    except Exception:
        pass

def _focus_key(filepath: Path, question: str, top_k: int) -> str:
    q_hash = hashlib.md5(question.encode()).hexdigest()[:8]
    return f'{filepath.resolve()}:{filepath.stat().st_mtime}:{q_hash}:{top_k}'

STOP_WORDS = {
    'the','a','an','is','are','was','were','be','been','being','have','has','had',
    'do','does','did','will','would','could','should','may','might','can',
    'how','what','where','when','why','who','which','that','this','these','those',
    'i','you','he','she','it','we','they','me','him','her','us','them',
    'my','your','his','its','our','their','to','of','in','for','on','with',
    'at','by','from','as','into','and','but','or','not','if','then','about',
    'explain','describe','tell','show','find','make','get','use','look',
}


def keywords(text: str) -> set[str]:
    words = re.findall(r'[a-zA-Z][a-zA-Z0-9]*', text.lower())
    # Also split camelCase
    camel = []
    for w in re.findall(r'[a-zA-Z][a-zA-Z0-9]*', text):
        camel += [p.lower() for p in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', w)]
    return {w for w in set(words + camel) if w not in STOP_WORDS and len(w) > 2}


def split_chunks(content: str, size: int = 40) -> list[tuple[int, str]]:
    lines = content.split('\n')
    chunks, i = [], 0
    while i < len(lines):
        end = min(i + size, len(lines))
        # Try to end at a natural boundary
        if end < len(lines):
            for j in range(end, max(i + size // 2, end - 8), -1):
                if not lines[j].strip() or lines[j].startswith('#'):
                    end = j; break
        chunk = '\n'.join(lines[i:end])
        if chunk.strip():
            chunks.append((i + 1, chunk))
        i = end
    return chunks


def score(chunk: str, kws: set[str]) -> float:
    if not kws: return 0.0
    low = chunk.lower()
    hits = 0.0
    for kw in kws:
        c = low.count(kw)
        if c: hits += 1 + min(c - 1, 2) * 0.3
    # Bonus for keywords in headings
    for line in chunk.split('\n'):
        if line.startswith('#'):
            for kw in kws:
                if kw in line.lower(): hits += 1.5
    words = len(low.split())
    return hits / (1 + words / 100) if words else 0.0


def structure_overview(content: str, lines: list[str], filepath: Path) -> str:
    """Return headings + first line of each section when no question given."""
    kept = []
    in_code = False
    for i, line in enumerate(lines):
        if line.strip().startswith('```'): in_code = not in_code; continue
        if in_code: continue
        if line.startswith('#'):
            kept.append(line)
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip(): kept.append(lines[j]); break
        elif re.match(r'^(def |class |function |const |export |import |func )', line):
            kept.append(line)
    return (f"[PITH FOCUS: {filepath.name} — {len(lines)} lines, structural overview]\n\n"
            + '\n'.join(kept) + "\n\n[Ask about a specific section]")


def focus(filepath: Path, question: str, top_k: int = 5) -> str:
    if not filepath.exists():
        return f"[PITH FOCUS ERROR: file not found: {filepath}]"

    cache = _load_focus_cache()
    key   = _focus_key(filepath, question, top_k)
    if key in cache:
        return cache[key]

    content = filepath.read_text(errors='ignore')
    lines   = content.split('\n')

    def _cache_and_return(result: str) -> str:
        cache[key] = result
        _save_focus_cache(cache)
        return result

    if len(lines) <= 60:
        return _cache_and_return(
            f"[PITH FOCUS: {filepath.name} — {len(lines)} lines, returned in full]\n\n{content}"
        )

    kws = keywords(question) if question.strip() else set()
    if not kws:
        return _cache_and_return(structure_overview(content, lines, filepath))

    chunks  = split_chunks(content)
    scored  = [(score(chunk, kws), ln, chunk) for ln, chunk in chunks]
    top     = sorted(scored, key=lambda x: -x[0])[:top_k]

    if all(s == 0.0 for s, _, _ in top):
        return _cache_and_return(structure_overview(content, lines, filepath))

    top_ordered = sorted(top, key=lambda x: x[1])
    parts = [f"[PITH FOCUS: {filepath.name} — {len(lines)} lines → {top_k} relevant sections for: \"{question[:80]}\"]\n"]
    for s, ln, chunk in top_ordered:
        parts.append(f"[~Line {ln}]\n{chunk}")
    parts.append(f"\n[{len(chunks) - top_k} other sections omitted. Ask for full file or specific section.]")
    return _cache_and_return('\n\n'.join(parts))


def main():
    import os
    p = argparse.ArgumentParser()
    p.add_argument('filepath', nargs='?')
    p.add_argument('--stdin-path', action='store_true',
                   help='Read filepath from stdin (first line). Used by the /focus '
                        'slash-command to bypass shell-argument quoting entirely.')
    p.add_argument('--question', '-q', default='')
    p.add_argument('--top', '-k', type=int, default=5)
    args = p.parse_args()
    if args.stdin_path:
        raw = sys.stdin.read().strip().splitlines()
        if not raw:
            print('[PITH FOCUS: no path on stdin]')
            return
        filepath = raw[0].strip()
    else:
        filepath = args.filepath
    if not filepath:
        p.error('filepath is required (positional, or via --stdin-path)')
    filepath = Path(filepath).resolve()
    cwd = Path(os.environ.get('CLAUDE_CWD') or os.getcwd()).resolve()
    if not str(filepath).startswith(str(cwd)):
        print(f"[PITH FOCUS: access denied — {filepath.name} is outside the project directory]")
        sys.exit(1)
    print(focus(filepath, args.question, args.top))

if __name__ == '__main__':
    main()
