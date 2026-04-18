#!/usr/bin/env python3
"""
Pith Compile — batch re-synthesis of wiki from all ingested sources.

Reads every file in raw/sources/, synthesizes cross-source wiki pages,
updates existing pages with new evidence, files back to wiki/.

Usage:
    python3 compile.py
    python3 compile.py --topic "authentication"   # recompile one topic only
    python3 compile.py --dry-run                  # show plan, write nothing
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys as _sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent))
from _safe_paths import safe_wiki_path, UnsafePathError  # noqa: E402
from datetime import datetime, timezone
from pathlib import Path

COMPILE_PLAN_PROMPT = """You are maintaining a project knowledge wiki.

Below are all source documents that have been ingested. Your job:
1. Identify the key topics that span multiple sources
2. For each topic, list which sources cover it
3. Identify gaps — things implied by sources but not yet in the wiki
4. Suggest new synthesis pages to create

SOURCES (titles and summaries):
{source_summaries}

EXISTING WIKI INDEX:
{wiki_index}

OUTPUT THIS JSON (no other text):
{{
  "topics": [
    {{
      "name": "Topic Name",
      "sources": ["source-file-1.md", "source-file-2.md"],
      "existing_page": "wiki/concepts/topic.md or null",
      "action": "update | create | skip",
      "reason": "why this needs a (new) page"
    }}
  ],
  "gaps": [
    {{
      "description": "what is missing",
      "suggested_page": "wiki/concepts/suggested-name.md",
      "referenced_in": ["source-file.md"]
    }}
  ],
  "synthesis_pages": [
    {{
      "path": "wiki/concepts/cross-source-synthesis.md",
      "title": "Page Title",
      "thesis": "one-sentence claim this page will argue",
      "sources": ["source1.md", "source2.md"]
    }}
  ]
}}"""

COMPILE_PAGE_INSTRUCTIONS = """Write or update a wiki page synthesizing evidence from multiple sources.

Rules:
- Lead with the thesis
- Cite every claim: [source-name](../../raw/sources/file.md)
- Cross-link related pages with [[PageName]]
- Add Counter-evidence section if sources disagree
- Keep it tight — no prose where structure works

Return only the markdown content."""

COMPILE_PAGE_PROMPT = """Write or update a wiki page synthesizing evidence from multiple sources.

Page: {page_path}
Title: {title}
Thesis: {thesis}

SOURCES (relevant excerpts):
{source_excerpts}

EXISTING PAGE CONTENT (if any):
{existing_content}

Rules:
- Lead with the thesis
- Cite every claim: [source-name](../../raw/sources/file.md)
- Cross-link related pages with [[PageName]]
- Add Counter-evidence section if sources disagree
- Keep it tight — no prose where structure works

Return only the markdown content."""

SYNTHESIS_FORMAT = """# {title}

**Thesis:** {thesis}
**Confidence:** medium
**Sources:** {source_count} documents
**Last compiled:** {date}

## Evidence
- [point] — [source](../../raw/sources/file.md)

## Counter-evidence
- [conflicting point if any]

## Open questions
- [what would change this conclusion]

## Related
- [[concept]] — [relation]
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)


@contextmanager
def _compile_lock(cwd: Path):
    lock_path = cwd / 'wiki' / '.compile.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'w') as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('[PITH COMPILE: another compile is already running — aborting]')
            raise SystemExit(1)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
            lock_path.unlink(missing_ok=True)


_MANIFEST_FILE = '.compile-manifest.json'

def _load_manifest(cwd: Path) -> dict:
    p = cwd / 'wiki' / _MANIFEST_FILE
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}

def _save_manifest(cwd: Path, manifest: dict) -> None:
    p = cwd / 'wiki' / _MANIFEST_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, json.dumps(manifest, indent=2))

def _source_fingerprint(path: Path) -> str:
    s = path.stat()
    return f'{s.st_mtime}:{s.st_size}'


def call_claude(prompt: str) -> str:
    if key := os.environ.get('ANTHROPIC_API_KEY'):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model=os.environ.get('PITH_MODEL', 'claude-sonnet-4-6'),
                max_tokens=4096,
                messages=[{'role': 'user', 'content': prompt}],
            )
            return msg.content[0].text.strip()
        except ImportError:
            pass
    try:
        r = subprocess.run(['claude', '--print'], input=prompt, text=True,
                           capture_output=True, check=True, timeout=120)
        return r.stdout.strip()
    except Exception as e:
        raise RuntimeError(f'Claude call failed: {e}')


def call_claude_page(dynamic_spec: str) -> str:
    """Call Claude for page synthesis with prompt caching on the static instructions."""
    if key := os.environ.get('ANTHROPIC_API_KEY'):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model=os.environ.get('PITH_MODEL', 'claude-sonnet-4-6'),
                max_tokens=4096,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': COMPILE_PAGE_INSTRUCTIONS,
                            'cache_control': {'type': 'ephemeral'},
                        },
                        {'type': 'text', 'text': dynamic_spec},
                    ],
                }],
            )
            return msg.content[0].text.strip()
        except ImportError:
            pass
    return call_claude(COMPILE_PAGE_INSTRUCTIONS + '\n\n' + dynamic_spec)


def read_wiki_index(cwd: Path) -> str:
    idx = cwd / 'wiki' / 'index.md'
    return idx.read_text(errors='ignore') if idx.exists() else '(no wiki index yet)'


def summarize_sources(sources_dir: Path, topic_filter: str | None = None) -> list[dict]:
    """Return list of {filename, title, excerpt} for each source."""
    results = []
    if not sources_dir.exists():
        return results
    for f in sorted(sources_dir.glob('*.md')):
        text = f.read_text(errors='ignore')
        title = f.stem.replace('-', ' ').replace('_', ' ').title()
        # Use first heading if present
        m = re.search(r'^#\s+(.+)', text, re.MULTILINE)
        if m:
            title = m.group(1).strip()
        excerpt = text[:600].replace('\n', ' ')
        if topic_filter and topic_filter.lower() not in text.lower():
            continue
        results.append({'filename': f.name, 'title': title, 'excerpt': excerpt, 'path': f})
    return results


def get_source_excerpts(sources: list[dict], topic: str, max_chars: int = 3000) -> str:
    parts = []
    total = 0
    for s in sources:
        text = s['path'].read_text(errors='ignore')
        # Find most relevant 800 chars
        idx = text.lower().find(topic.lower())
        start = max(0, idx - 100) if idx >= 0 else 0
        chunk = text[start:start + 800]
        entry = f"### {s['title']} ({s['filename']})\n{chunk}\n"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        total += len(entry)
    return '\n'.join(parts) if parts else '(no excerpts)'


def update_index(cwd: Path, new_pages: list[dict]):
    idx_path = cwd / 'wiki' / 'index.md'
    content = idx_path.read_text(errors='ignore') if idx_path.exists() else '# Wiki Index\n'
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for page in new_pages:
        name = page.get('title', page.get('name', ''))
        rel = page.get('path', '').replace('wiki/', '')
        entry = f'- [[{name}]]({rel}) — _(compiled {now})_\n'
        if '## Syntheses\n' in content:
            content = content.replace('## Syntheses\n', '## Syntheses\n' + entry)
        else:
            content += f'\n## Syntheses\n{entry}'
    _atomic_write(idx_path, content)


def append_log(cwd: Path, created: list[str], updated: list[str], gaps: list[dict]):
    log_path = cwd / 'wiki' / 'log.md'
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    gap_lines = '\n'.join(f'  - {g["description"]}' for g in gaps) if gaps else '  none'
    entry = f"""
## [{now}] compile
Created: {', '.join(f'[[{p}]]' for p in created) or 'none'}
Updated: {', '.join(f'[[{p}]]' for p in updated) or 'none'}
Gaps identified:
{gap_lines}
"""
    existing = log_path.read_text() if log_path.exists() else '# Wiki Log\n'
    _atomic_write(log_path, existing + entry)


def _write_page(spec: dict, sources: list[dict], cwd: Path) -> tuple[str, str]:
    """Write one synthesis page. Returns ('created'|'updated', title). Thread-safe."""
    try:
        page_path = safe_wiki_path(cwd, spec.get('path'))
    except UnsafePathError as exc:
        raise ValueError(f'skipped: {exc}')
    page_path.parent.mkdir(parents=True, exist_ok=True)
    relevant = [s for s in sources if s['filename'] in spec.get('sources', [])] or sources[:3]
    excerpts = get_source_excerpts(relevant, spec.get('title', ''))
    existing = page_path.read_text(errors='ignore') if page_path.exists() else ''
    dynamic  = (
        f"Page: {spec['path']}\nTitle: {spec['title']}\nThesis: {spec.get('thesis', '')}\n\n"
        f"SOURCES (relevant excerpts):\n{excerpts}\n\n"
        f"EXISTING PAGE CONTENT (if any):\n{existing[:2000] if existing else '(new page)'}"
    )
    content = call_claude_page(dynamic)
    _atomic_write(page_path, content)
    return ('updated' if existing else 'created', spec['title'])


def compile_wiki(topic_filter: str | None = None, dry_run: bool = False):
    cwd = Path.cwd()
    sources_dir = cwd / 'raw' / 'sources'

    sources = summarize_sources(sources_dir, topic_filter)
    if not sources:
        print('[PITH COMPILE: no sources found in raw/sources/]')
        print('Run /pith ingest <file> or /pith ingest --url <url> to add sources first.')
        return

    with _compile_lock(cwd):
        manifest = _load_manifest(cwd)
        new_manifest: dict = {}

        # Fingerprint each source; track which changed
        changed_sources, unchanged = [], []
        for s in sources:
            fp = _source_fingerprint(s['path'])
            new_manifest[s['filename']] = fp
            if manifest.get(s['filename']) != fp:
                changed_sources.append(s)
            else:
                unchanged.append(s['filename'])

        if unchanged and not topic_filter:
            print(f'  {len(unchanged)} source(s) unchanged, skipping: {", ".join(unchanged[:5])}{"…" if len(unchanged) > 5 else ""}')

        if not changed_sources and not topic_filter:
            print('[PITH COMPILE: all sources up to date — nothing to recompile]')
            return

        active_sources = changed_sources if changed_sources else sources
        print(f'Found {len(active_sources)} changed source(s). Building compile plan...')

        source_summaries = '\n\n'.join(
            f'**{s["filename"]}**\n{s["excerpt"][:400]}' for s in active_sources
        )
        wiki_index = read_wiki_index(cwd)

        plan_raw = call_claude(COMPILE_PLAN_PROMPT.format(
            source_summaries=source_summaries[:6000],
            wiki_index=wiki_index[:2000],
        ))

        try:
            m = re.search(r'\{[\s\S]*\}', plan_raw)
            plan = json.loads(m.group()) if m else {}
        except Exception:
            plan = {}

        if not plan:
            print('[PITH COMPILE: plan generation failed]\n' + plan_raw)
            return

        topics          = [t for t in plan.get('topics', []) if t.get('action') != 'skip']
        synthesis_pages = plan.get('synthesis_pages', [])
        gaps            = plan.get('gaps', [])

        print(f'\nCompile plan:')
        print(f'  Topics to process: {len(topics)}')
        print(f'  Synthesis pages:   {len(synthesis_pages)}')
        print(f'  Gaps identified:   {len(gaps)}')

        if gaps:
            print('\nGaps (missing knowledge):')
            for g in gaps:
                print(f'  - {g["description"]}')
                print(f'    → suggested: {g.get("suggested_page", "??")}')

        if dry_run:
            print('\n[dry-run] No files written.')
            return

        created, updated = [], []
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        # Write synthesis pages in parallel (prompt caching amortizes across calls)
        if synthesis_pages:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_write_page, spec, active_sources, cwd): spec
                           for spec in synthesis_pages}
                for fut in as_completed(futures):
                    try:
                        action, title = fut.result()
                        (created if action == 'created' else updated).append(title)
                        print(f'  ✓ {action} {title}')
                    except Exception as exc:
                        print(f'  ✗ {futures[fut]["title"]}: {exc}')

        # Update existing topic pages (sequential — each may depend on previous)
        for topic in topics:
            if topic.get('action') != 'update':
                continue
            existing_path = topic.get('existing_page')
            if not existing_path:
                continue
            try:
                page_path = safe_wiki_path(cwd, existing_path)
            except UnsafePathError as exc:
                print(f'  ⚠ skipped: {exc}')
                continue
            if not page_path.exists():
                continue
            relevant = [s for s in active_sources if s['filename'] in topic.get('sources', [])]
            if not relevant:
                continue
            excerpts = get_source_excerpts(relevant, topic['name'])
            print(f'  Updating {existing_path}...')
            existing = page_path.read_text(errors='ignore')
            dynamic  = (
                f"Page: {existing_path}\nTitle: {topic['name']}\n"
                f"Thesis: Updated synthesis of {topic['name']}\n\n"
                f"SOURCES (relevant excerpts):\n{excerpts}\n\n"
                f"EXISTING PAGE CONTENT (if any):\n{existing[:2000]}"
            )
            _atomic_write(page_path, call_claude_page(dynamic))
            updated.append(topic['name'])
            print(f'    ✓')

        update_index(cwd, [{'title': t, 'path': s['path']} for t, s in
                            zip(created, synthesis_pages[:len(created)])])
        append_log(cwd, created, updated, gaps)
        _save_manifest(cwd, new_manifest)

        print(f'\nCompile complete.')
        print(f'  Created: {len(created)}  Updated: {len(updated)}  Gaps filed: {len(gaps)}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--topic', help='Recompile only pages matching this topic')
    p.add_argument('--dry-run', action='store_true', help='Show plan without writing')
    args = p.parse_args()
    compile_wiki(topic_filter=args.topic, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
