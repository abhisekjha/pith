#!/usr/bin/env python3
"""
Pith Lint — semantic health checks for the project wiki.

Checks:
  - Orphan pages (no inbound links)
  - Pages with no sources
  - Cross-page contradictions
  - Entities mentioned but lacking their own page
  - Stale unresolved contradictions (>30 days)
  - Missing cross-links between related pages
  - Gaps: topics implied by sources but not yet covered

Usage:
    python3 lint.py
    python3 lint.py --fix      # auto-create stub pages for missing entities
    python3 lint.py --quick    # structural checks only, no LLM call
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)


_LINT_CACHE_FILE = '.lint-cache.json'

def _wiki_fingerprint(pages: list[dict]) -> str:
    h = hashlib.sha256()
    for p in sorted(pages, key=lambda x: x['path']):
        h.update(p['path'].encode())
        try:
            mtime = str(Path(p['path']).stat().st_mtime)
        except Exception:
            mtime = '0'
        h.update(mtime.encode())
    return h.hexdigest()

def _load_lint_cache(cwd: Path) -> dict:
    p = cwd / 'wiki' / _LINT_CACHE_FILE
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}

def _save_lint_cache(cwd: Path, fingerprint: str, result: dict) -> None:
    p = cwd / 'wiki' / _LINT_CACHE_FILE
    _atomic_write(p, json.dumps({'fingerprint': fingerprint, 'result': result}))

LINT_PROMPT = """You are auditing a project knowledge wiki for quality issues.

WIKI PAGES (titles and content summaries):
{page_summaries}

WIKI INDEX:
{wiki_index}

LOG (recent operations):
{log_tail}

Find ALL of the following issues:

1. **Contradictions** — claims in different pages that conflict
2. **Missing entities** — names mentioned in pages that have no dedicated page
3. **Suggested connections** — pages that should cross-link but don't
4. **Knowledge gaps** — topics heavily referenced but poorly covered
5. **Imputable data** — facts that can be inferred from existing pages but aren't stated

OUTPUT THIS JSON (no other text):
{{
  "contradictions": [
    {{
      "page_a": "wiki/path/a.md",
      "claim_a": "exact claim from page A",
      "page_b": "wiki/path/b.md",
      "claim_b": "conflicting claim from page B",
      "action": "review and resolve"
    }}
  ],
  "missing_entities": [
    {{
      "name": "EntityName",
      "mentioned_in": ["wiki/page.md"],
      "suggested_path": "wiki/entities/EntityName.md",
      "why": "referenced 3 times but has no page"
    }}
  ],
  "suggested_connections": [
    {{
      "page_a": "wiki/path/a.md",
      "page_b": "wiki/path/b.md",
      "reason": "both discuss X but don't link to each other"
    }}
  ],
  "knowledge_gaps": [
    {{
      "topic": "topic name",
      "evidence": "referenced in N pages but coverage is thin",
      "suggested_page": "wiki/concepts/topic.md"
    }}
  ],
  "imputable_facts": [
    {{
      "page": "wiki/path/page.md",
      "fact": "fact that can be inferred from existing wiki content",
      "source_pages": ["wiki/path/a.md"]
    }}
  ]
}}"""

STUB_PAGE = """# {name}

**Type:** entity
**Summary:** _(stub — created by lint)_

## Key Facts
- _(to be filled)_

## Sources
- _(none yet — mentioned in {mentioned_in})_
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
                           capture_output=True, check=True, timeout=90)
        return r.stdout.strip()
    except Exception as e:
        raise RuntimeError(f'Claude call failed: {e}')


def collect_wiki_pages(cwd: Path) -> list[dict]:
    wiki_dir = cwd / 'wiki'
    if not wiki_dir.exists():
        return []
    pages = []
    for f in sorted(wiki_dir.rglob('*.md')):
        if f.name in ('index.md', 'log.md', 'overview.md'):
            continue
        text = f.read_text(errors='ignore')
        rel = str(f.relative_to(cwd))
        title_m = re.search(r'^#\s+(.+)', text, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else f.stem
        links = re.findall(r'\[\[([^\]]+)\]\]', text)
        pages.append({
            'path': rel,
            'title': title,
            'text': text,
            'links': links,
            'has_sources': '## Sources' in text and '_(none' not in text,
        })
    return pages


def structural_checks(pages: list[dict], wiki_index: str) -> list[str]:
    issues = []
    all_paths = {p['path'] for p in pages}
    all_titles = {p['title'] for p in pages}

    # Build inbound link count
    inbound: dict[str, int] = {p['path']: 0 for p in pages}
    for p in pages:
        for link in p['links']:
            for other in pages:
                if link.lower() in other['title'].lower() or link.lower() in other['path'].lower():
                    inbound[other['path']] = inbound.get(other['path'], 0) + 1

    # Orphans
    for p in pages:
        if inbound.get(p['path'], 0) == 0 and p['path'] not in wiki_index:
            issues.append(f'ORPHAN  {p["path"]} — no inbound links')

    # No sources
    for p in pages:
        if not p['has_sources']:
            issues.append(f'NO-SRC  {p["path"]} — missing Sources section')

    # index.md entries pointing to non-existent files
    for m in re.finditer(r'\(([^)]+\.md)\)', wiki_index):
        ref = m.group(1)
        full = str(Path('wiki') / ref)
        if not Path(full).exists() and ref not in all_paths:
            issues.append(f'BROKEN  index.md → {ref} (file missing)')

    # Stale unresolved contradictions in log.md
    log_path = Path('wiki/log.md')
    if log_path.exists():
        log_text = log_path.read_text(errors='ignore')
        for m in re.finditer(r'\[(\d{4}-\d{2}-\d{2})\].*\nContradictions: yes', log_text):
            date_str = m.group(1)
            try:
                date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - date).days
                if age > 30:
                    issues.append(f'STALE   log entry {date_str} — unresolved contradiction ({age}d old)')
            except ValueError:
                pass

    return issues


def append_lint_log(cwd: Path, issue_count: int):
    log_path = cwd / 'wiki' / 'log.md'
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    entry = f'\n## [{now}] lint\nIssues found: {issue_count}\n'
    existing = log_path.read_text() if log_path.exists() else '# Wiki Log\n'
    _atomic_write(log_path, existing + entry)


def create_stub(cwd: Path, spec: dict):
    path = cwd / spec['suggested_path']
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(STUB_PAGE.format(
            name=spec['name'],
            mentioned_in=', '.join(spec.get('mentioned_in', [])),
        ))
        print(f'  + Created stub: {spec["suggested_path"]}')


def lint(fix: bool = False, quick: bool = False):
    cwd = Path.cwd()
    wiki_dir = cwd / 'wiki'

    if not wiki_dir.exists():
        print('[PITH LINT: no wiki/ directory found — run /pith wiki to initialize]')
        return

    pages = collect_wiki_pages(cwd)
    idx_path = wiki_dir / 'index.md'
    wiki_index = idx_path.read_text(errors='ignore') if idx_path.exists() else ''
    log_path = wiki_dir / 'log.md'
    log_tail = log_path.read_text(errors='ignore')[-2000:] if log_path.exists() else ''

    print(f'Scanning {len(pages)} wiki pages...')

    # Always run structural checks
    structural = structural_checks(pages, wiki_index)

    if quick:
        total = len(structural)
        print(f'\n[PITH LINT] {total} structural issue(s)\n')
        for i, issue in enumerate(structural, 1):
            print(f'  {i}. {issue}')
        if total == 0:
            print('  No issues found.')
        return

    # Check fingerprint cache before firing LLM
    fingerprint  = _wiki_fingerprint(pages)
    lint_cache   = _load_lint_cache(cwd)
    cached_result: dict | None = None
    if lint_cache.get('fingerprint') == fingerprint and not fix:
        cached_result = lint_cache.get('result', {})
        print('  (wiki unchanged since last lint — using cached semantic results)')

    # LLM semantic checks
    if cached_result is not None:
        result = cached_result
    else:
        print('Running semantic analysis...')
        page_summaries = '\n\n'.join(
            f'**{p["path"]}** (links: {", ".join(p["links"][:5]) or "none"})\n{p["text"][:400]}'
            for p in pages[:20]
        )
        raw = call_claude(LINT_PROMPT.format(
            page_summaries=page_summaries[:8000],
            wiki_index=wiki_index[:2000],
            log_tail=log_tail[:1000],
        ))
        try:
            m = re.search(r'\{[\s\S]*\}', raw)
            result = json.loads(m.group()) if m else {}
        except Exception:
            result = {}
        _save_lint_cache(cwd, fingerprint, result)

    issue_num = 0

    def header(label: str, items: list):
        nonlocal issue_num
        if not items:
            return
        print(f'\n  ── {label} ──')
        for item in items:
            issue_num += 1
            yield issue_num, item

    print(f'\n[PITH LINT REPORT]\n')

    # Structural
    if structural:
        print('  ── Structural ──')
        for issue in structural:
            issue_num += 1
            print(f'  {issue_num}. {issue}')

    # Contradictions
    for n, c in header('Contradictions', result.get('contradictions', [])):
        print(f'  {n}. CONTRADICTION')
        print(f'     {c.get("page_a")}: "{c.get("claim_a")}"')
        print(f'     {c.get("page_b")}: "{c.get("claim_b")}"')
        print(f'     → {c.get("action")}')

    # Missing entities
    missing_entities = result.get('missing_entities', [])
    for n, e in header('Missing entity pages', missing_entities):
        print(f'  {n}. MISSING PAGE  {e.get("name")}')
        print(f'     Mentioned in: {", ".join(e.get("mentioned_in", []))}')
        print(f'     → Create: {e.get("suggested_path")}')
        if fix:
            create_stub(cwd, e)

    # Suggested connections
    for n, c in header('Suggested connections', result.get('suggested_connections', [])):
        print(f'  {n}. UNLINKED  {c.get("page_a")} ↔ {c.get("page_b")}')
        print(f'     → {c.get("reason")}')

    # Knowledge gaps
    for n, g in header('Knowledge gaps', result.get('knowledge_gaps', [])):
        print(f'  {n}. GAP  {g.get("topic")}')
        print(f'     {g.get("evidence")}')
        print(f'     → Create: {g.get("suggested_page")}')

    # Imputable facts
    for n, f_item in header('Imputable facts', result.get('imputable_facts', [])):
        print(f'  {n}. MISSING FACT  in {f_item.get("page")}')
        print(f'     "{f_item.get("fact")}"')
        print(f'     → Derivable from: {", ".join(f_item.get("source_pages", []))}')

    total = issue_num
    print(f'\n  {total} issue(s) found.')
    if fix and missing_entities:
        print(f'  Stubs created for {len(missing_entities)} missing entity page(s).')
    if not fix and any(result.get(k) for k in ('missing_entities',)):
        print('  Run with --fix to auto-create stub pages for missing entities.')

    append_lint_log(cwd, total)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--fix', action='store_true', help='Auto-create stubs for missing entity pages')
    p.add_argument('--quick', action='store_true', help='Structural checks only (no LLM)')
    args = p.parse_args()
    lint(fix=args.fix, quick=args.quick)


if __name__ == '__main__':
    main()
