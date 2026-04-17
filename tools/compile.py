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
import json
import os
import re
import subprocess
import sys as _sys
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
    idx_path.write_text(content)


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
    if log_path.exists():
        log_path.write_text(log_path.read_text() + entry)
    else:
        log_path.write_text('# Wiki Log\n' + entry)


def compile_wiki(topic_filter: str | None = None, dry_run: bool = False):
    cwd = Path.cwd()
    sources_dir = cwd / 'raw' / 'sources'

    sources = summarize_sources(sources_dir, topic_filter)
    if not sources:
        print('[PITH COMPILE: no sources found in raw/sources/]')
        print('Run /pith ingest <file> or /pith ingest --url <url> to add sources first.')
        return

    print(f'Found {len(sources)} source(s). Building compile plan...')

    source_summaries = '\n\n'.join(
        f'**{s["filename"]}**\n{s["excerpt"][:400]}' for s in sources
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

    topics = [t for t in plan.get('topics', []) if t.get('action') != 'skip']
    synthesis_pages = plan.get('synthesis_pages', [])
    gaps = plan.get('gaps', [])

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

    # Write synthesis pages
    for spec in synthesis_pages:
        try:
            page_path = safe_wiki_path(cwd, spec.get('path'))
        except UnsafePathError as exc:
            print(f'  ⚠ skipped: {exc}')
            continue
        page_path.parent.mkdir(parents=True, exist_ok=True)

        relevant_sources = [s for s in sources if s['filename'] in spec.get('sources', [])]
        if not relevant_sources:
            relevant_sources = sources[:3]

        excerpts = get_source_excerpts(relevant_sources, spec.get('title', ''))
        existing = page_path.read_text(errors='ignore') if page_path.exists() else ''
        action = 'Updating' if existing else 'Creating'
        print(f'  {action} {spec["path"]}...')

        content = call_claude(COMPILE_PAGE_PROMPT.format(
            page_path=spec['path'],
            title=spec['title'],
            thesis=spec.get('thesis', ''),
            source_excerpts=excerpts,
            existing_content=existing[:2000] if existing else '(new page)',
        ))
        page_path.write_text(content)

        if existing:
            updated.append(spec['title'])
        else:
            created.append(spec['title'])
        print(f'    ✓')

    # Update existing topic pages
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
        relevant_sources = [s for s in sources if s['filename'] in topic.get('sources', [])]
        if not relevant_sources:
            continue
        excerpts = get_source_excerpts(relevant_sources, topic['name'])
        print(f'  Updating {existing_path}...')
        content = call_claude(COMPILE_PAGE_PROMPT.format(
            page_path=existing_path,
            title=topic['name'],
            thesis=f'Updated synthesis of {topic["name"]}',
            source_excerpts=excerpts,
            existing_content=page_path.read_text(errors='ignore')[:2000],
        ))
        page_path.write_text(content)
        updated.append(topic['name'])
        print(f'    ✓')

    update_index(cwd, [{'title': t, 'path': s['path']} for t, s in
                        zip(created, synthesis_pages[:len(created)])])
    append_log(cwd, created, updated, gaps)

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
