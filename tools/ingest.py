#!/usr/bin/env python3
"""
Pith Ingest — add a source document or URL to the project wiki.
Extracts entities, claims, contradictions, and updates wiki pages.

Usage:
    python3 ingest.py <filepath>
    python3 ingest.py raw/sources/article.md
    python3 ingest.py --url https://example.com/article
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import urllib.error  # still referenced for urllib.error.URLError
from datetime import datetime, timezone
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from _safe_paths import safe_wiki_path, UnsafePathError  # noqa: E402
from _safe_fetch import safe_fetch, UnsafeFetchError  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)


_INGEST_MANIFEST = 'wiki/.ingest-manifest.json'

def _load_ingest_manifest(cwd: Path) -> dict:
    p = cwd / _INGEST_MANIFEST
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}

def _save_ingest_manifest(cwd: Path, manifest: dict) -> None:
    p = cwd / _INGEST_MANIFEST
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, json.dumps(manifest, indent=2))

def _source_fp(path: Path) -> str:
    s = path.stat()
    return f'{s.st_mtime}:{s.st_size}'

def _already_ingested(cwd: Path, filepath: Path) -> bool:
    manifest = _load_ingest_manifest(cwd)
    key = str(filepath.resolve())
    return manifest.get(key) == _source_fp(filepath)

CODE_EXTENSIONS = {
    'py', 'js', 'ts', 'jsx', 'tsx', 'mjs', 'cjs',
    'go', 'java', 'kt', 'rs', 'cs', 'cpp', 'c', 'h',
    'rb', 'php', 'swift',
}

CODE_MUNCH_PROMPT = """You are maintaining a project knowledge wiki. A code file has been added.

Extract the structural knowledge — what this module exports, what its classes do,
key architectural dependencies. NOT implementation details.

CODE SKELETON (imports + signatures from symbols.py):
{skeleton}

FILE HEAD (first 120 lines for context):
{head}

EXISTING WIKI INDEX:
{wiki_index}

OUTPUT THIS JSON (no other text):
{{
  "title": "module or file title",
  "summary": "1-2 sentences: what this module does and why it exists",
  "module_name": "the primary module/class/package name",
  "exports": ["list of exported functions, classes, or constants"],
  "classes": [
    {{"name": "ClassName", "purpose": "one sentence", "key_methods": ["method1", "method2"]}}
  ],
  "dependencies": ["imported modules/packages that matter architecturally"],
  "key_claims": ["important facts about this code's behaviour or contract"],
  "contradictions": ["anything conflicting with existing wiki, or empty"],
  "update_pages": ["existing wiki page paths to update"],
  "create_pages": [
    {{"path": "wiki/entities/ModuleName.md", "type": "module", "name": "ModuleName"}},
    {{"path": "wiki/entities/ClassName.md",  "type": "class",  "name": "ClassName", "module": "ModuleName"}}
  ]
}}"""

MODULE_FORMAT = """# {name}

**Type:** module
**File:** `{filepath}`
**Summary:** [one sentence]

## Exports
- `[symbol]` — [one line purpose]

## Dependencies
- [[related]] — [why it depends on this]

## Notes
- [architectural decision or constraint worth knowing]

## Sources
- [{title}]({source_path}) — {date}"""

CLASS_FORMAT = """# {name}

**Type:** class
**Module:** [[{module}]]
**Summary:** [one sentence]

## Key Methods
| Method | Purpose |
|--------|---------|
| `method()` | [one line] |

## Relationships
- [[related]] — [how related]

## Sources
- [{title}]({source_path}) — {date}"""

INGEST_PROMPT = """You are maintaining a project knowledge wiki. A new source document has been added.

Your job:
1. Extract key information from the source
2. Identify which existing wiki pages should be updated
3. Identify what new pages should be created
4. Flag any contradictions with existing knowledge

SOURCE DOCUMENT:
{source_content}

EXISTING WIKI INDEX:
{wiki_index}

OUTPUT THIS JSON (no other text):
{{
  "title": "source title or filename",
  "summary": "2-3 sentence summary",
  "entities": ["list of named entities: people, tools, services, companies"],
  "concepts": ["list of key concepts/ideas"],
  "key_claims": ["list of important claims or facts"],
  "contradictions": ["claim in this source that conflicts with existing wiki, or empty"],
  "update_pages": ["list of existing wiki page paths to update"],
  "create_pages": [
    {{"path": "wiki/entities/EntityName.md", "type": "entity", "name": "EntityName"}},
    {{"path": "wiki/concepts/concept-name.md", "type": "concept", "name": "Concept Name"}}
  ]
}}"""

WRITE_PAGE_PROMPT = """Write a wiki page for this {page_type}.

Page path: {page_path}
Page name: {page_name}
Source information:
{source_summary}

Key claims from source:
{key_claims}

Follow this format exactly:

{page_format}

Keep it concise. No prose where structure works. Cross-link related pages with [[PageName]] syntax.
Return only the markdown content, no explanation."""

ENTITY_FORMAT = """# {name}

**Type:** [person|org|tool|service|component]
**Summary:** [2 sentences max]

## Key Facts
- [fact from source]

## Connections
- [[related]] — [how related]

## Sources
- [{title}]({source_path}) — {date}"""

CONCEPT_FORMAT = """# {name}

**Definition:** [one sentence]
**Why it matters:** [one sentence]

## How it works
[2-3 sentences max]

## Related
- [[concept]] — [relation]

## Sources
- [{title}]({source_path}) — {date}"""


def get_code_skeleton(filepath: Path) -> str:
    """Use symbols.py --list to get the structural skeleton of a code file."""
    symbols_py = Path(__file__).parent / 'symbols.py'
    if symbols_py.exists():
        try:
            r = subprocess.run(
                ['python3', str(symbols_py), '--list', str(filepath)],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    # Fallback: first 80 lines
    return '\n'.join(filepath.read_text(errors='ignore').split('\n')[:80])


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
                           capture_output=True, check=True, timeout=60)
        return r.stdout.strip()
    except Exception as e:
        raise RuntimeError(f'Claude call failed: {e}')


def read_wiki_index(cwd: Path) -> str:
    idx = cwd / 'wiki' / 'index.md'
    return idx.read_text(errors='ignore') if idx.exists() else '(no wiki index yet)'


def update_index(cwd: Path, new_pages: list[dict], source_title: str, source_path: str):
    idx_path = cwd / 'wiki' / 'index.md'
    content  = idx_path.read_text(errors='ignore') if idx_path.exists() else '# Wiki Index\n'
    now      = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    for page in new_pages:
        name      = page.get('name', '')
        page_type = page.get('type', 'concept')
        rel_path  = page.get('path', '')
        link_path = rel_path.replace('wiki/', '')
        entry     = f'- [[{name}]]({link_path}) — _(new)_\n'

        section = '## Entities\n' if page_type == 'entity' else '## Concepts\n'
        if section in content:
            content = content.replace(section, section + entry)
        else:
            content += f'\n{section}{entry}'

    # Add source to Sources Processed
    source_entry = f'- [{source_title}]({source_path}) — {now}\n'
    if '## Sources Processed\n' in content:
        content = content.replace('## Sources Processed\n', f'## Sources Processed\n{source_entry}')
        content = content.replace('_(none yet)_\n', '')
    else:
        content += f'\n## Sources Processed\n{source_entry}'

    _atomic_write(idx_path, content)


def append_log(cwd: Path, source_title: str, analysis: dict):
    log_path  = cwd / 'wiki' / 'log.md'
    now       = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    updated   = ', '.join(f'[[{p}]]' for p in analysis.get('update_pages', []))
    new_pages = ', '.join(f'[[{p["name"]}]]' for p in analysis.get('create_pages', []))
    contradictions = analysis.get('contradictions', [])

    entry = f"""
## [{now}] ingest | {source_title}
Pages updated: {updated or 'none'}
New pages: {new_pages or 'none'}
Contradictions: {'yes — ' + '; '.join(contradictions) if contradictions else 'none'}
"""
    existing = log_path.read_text() if log_path.exists() else '# Wiki Log\n'
    _atomic_write(log_path, existing + entry)


def ingest(filepath: Path, force: bool = False):
    cwd = Path.cwd()

    if not filepath.exists():
        print(f'[PITH INGEST ERROR: file not found: {filepath}]')
        return

    if not force and _already_ingested(cwd, filepath):
        print(f'[PITH INGEST: {filepath.name} already ingested and unchanged — skipping]')
        print(f'  Use --force to re-ingest.')
        return

    source_content = filepath.read_text(errors='ignore')
    wiki_index     = read_wiki_index(cwd)
    source_title   = filepath.stem.replace('-', ' ').replace('_', ' ').title()
    now            = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    ext     = filepath.suffix.lower().lstrip('.')
    is_code = ext in CODE_EXTENSIONS

    if is_code:
        print(f'[jCodeMunch] Code file detected ({ext}) — using structure-aware analysis...')
        skeleton = get_code_skeleton(filepath)
        head     = '\n'.join(source_content.split('\n')[:120])
        prompt   = CODE_MUNCH_PROMPT.format(
            skeleton=skeleton[:4000],
            head=head[:3000],
            wiki_index=wiki_index[:2000],
        )
    else:
        print(f'Analyzing {filepath.name}...')
        prompt = INGEST_PROMPT.format(
            source_content=source_content[:8000],
            wiki_index=wiki_index[:3000],
        )

    # Step 1: Extract information
    analysis_raw = call_claude(prompt)

    try:
        # Extract JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', analysis_raw)
        analysis = json.loads(json_match.group()) if json_match else {}
    except Exception:
        analysis = {}

    if not analysis:
        print(f'[PITH INGEST: analysis failed, showing raw output]\n{analysis_raw}')
        return

    # Show summary to user
    print(f"""
Source: {source_title}
Summary: {analysis.get('summary', '...')}

Found:
  Entities:   {', '.join(analysis.get('entities', [])) or 'none'}
  Concepts:   {', '.join(analysis.get('concepts', [])) or 'none'}
  Key claims: {len(analysis.get('key_claims', []))}

Will update: {', '.join(analysis.get('update_pages', [])) or 'none'}
Will create: {', '.join(p['name'] for p in analysis.get('create_pages', [])) or 'none'}
Contradictions: {'; '.join(analysis.get('contradictions', [])) or 'none'}

Writing pages...""")

    # Step 2: Write new pages
    rel_source = str(filepath.relative_to(cwd)) if filepath.is_relative_to(cwd) else str(filepath)

    for page_spec in analysis.get('create_pages', []):
        try:
            page_path = safe_wiki_path(cwd, page_spec.get('path'))
        except UnsafePathError as exc:
            print(f'  ⚠ skipped: {exc}')
            continue
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_type = page_spec.get('type', 'concept')
        if page_type == 'module':
            fmt = MODULE_FORMAT
            fmt_filled = fmt.format(
                name=page_spec['name'],
                filepath=str(filepath.relative_to(cwd)) if filepath.is_relative_to(cwd) else str(filepath),
                title=source_title,
                source_path='../../' + rel_source,
                date=now,
            )
        elif page_type == 'class':
            fmt = CLASS_FORMAT
            fmt_filled = fmt.format(
                name=page_spec['name'],
                module=page_spec.get('module', source_title),
                title=source_title,
                source_path='../../' + rel_source,
                date=now,
            )
        elif page_type == 'entity':
            fmt = ENTITY_FORMAT
            fmt_filled = fmt.format(
                name=page_spec['name'],
                title=source_title,
                source_path='../../' + rel_source,
                date=now,
            )
        else:
            fmt = CONCEPT_FORMAT
            fmt_filled = fmt.format(
                name=page_spec['name'],
                title=source_title,
                source_path='../../' + rel_source,
                date=now,
            )
        content = call_claude(WRITE_PAGE_PROMPT.format(
            page_type=page_type,
            page_path=page_spec['path'],
            page_name=page_spec['name'],
            source_summary=analysis.get('summary', ''),
            key_claims='\n'.join(f'- {c}' for c in analysis.get('key_claims', [])),
            page_format=fmt_filled,
        ))
        _atomic_write(page_path, content)
        print(f'  ✓ {page_spec["path"]}')

    # Step 3: Update index, log, and manifest
    update_index(cwd, analysis.get('create_pages', []), source_title, rel_source)
    append_log(cwd, source_title, analysis)
    manifest = _load_ingest_manifest(cwd)
    manifest[str(filepath.resolve())] = _source_fp(filepath)
    _save_ingest_manifest(cwd, manifest)

    print(f'\nIngest complete. {len(analysis.get("create_pages", []))} pages created.')

    # Step 4: Refresh GrepAI index if installed (silent — never block ingest)
    import shutil as _shutil
    wiki_dir = cwd / 'wiki'
    if _shutil.which('grepai') and wiki_dir.exists():
        try:
            subprocess.run(
                ['grepai', 'index', str(wiki_dir)],
                capture_output=True, timeout=60,
            )
            print('  ↻ GrepAI index refreshed.')
        except Exception:
            pass


def _html_to_text(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#?\w+;', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_url(url: str) -> Path:
    """Fetch URL, save as markdown in raw/sources/, return path.

    Uses `safe_fetch` to enforce http(s)-only + public-IP + size cap, which
    closes the SSRF path (`file://`, loopback, cloud-metadata IPs, …).
    """
    cwd = Path.cwd()
    sources_dir = cwd / 'raw' / 'sources'
    sources_dir.mkdir(parents=True, exist_ok=True)

    print(f'Fetching {url} ...')
    try:
        raw, content_type = safe_fetch(url)
    except UnsafeFetchError as e:
        raise RuntimeError(f'Refused to fetch {url}: {e}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Fetch failed: {e}')

    encoding = 'utf-8'
    m = re.search(r'charset=([^\s;]+)', content_type)
    if m:
        encoding = m.group(1).strip()

    text = raw.decode(encoding, errors='replace')

    if 'html' in content_type.lower():
        # Extract title
        title_m = re.search(r'<title[^>]*>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
        title = title_m.group(1).strip() if title_m else url
        title = re.sub(r'<[^>]+>', '', title).strip()
        text = _html_to_text(text)
        content = f'# {title}\n\nSource: {url}\nFetched: {datetime.now(timezone.utc).strftime("%Y-%m-%d")}\n\n{text}'
    else:
        title = url.rstrip('/').split('/')[-1] or 'fetched'
        content = text

    slug = re.sub(r'[^a-z0-9]+', '-', title.lower())[:60].strip('-')
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    out_path = sources_dir / f'{now}-{slug}.md'
    out_path.write_text(content, encoding='utf-8')
    print(f'Saved → {out_path.relative_to(cwd)}')
    return out_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('filepath', nargs='?', help='Local file to ingest')
    p.add_argument('--url', help='URL to fetch and ingest')
    p.add_argument('--force', action='store_true', help='Re-ingest even if unchanged')
    args = p.parse_args()

    if args.url:
        path = fetch_url(args.url)
        ingest(path, force=args.force)
    elif args.filepath:
        ingest(Path(args.filepath).resolve(), force=args.force)
    else:
        p.error('Provide a filepath or --url')

if __name__ == '__main__':
    main()
