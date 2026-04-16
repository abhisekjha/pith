#!/usr/bin/env python3
"""
Pith Ingest — add a source document to the project wiki.
Extracts entities, claims, contradictions, and updates wiki pages.

Usage:
    python3 ingest.py <filepath>
    python3 ingest.py raw/sources/article.md
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

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

    idx_path.write_text(content)


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
    if log_path.exists():
        log_path.write_text(log_path.read_text() + entry)
    else:
        log_path.write_text('# Wiki Log\n' + entry)


def ingest(filepath: Path):
    cwd = Path.cwd()

    if not filepath.exists():
        print(f'[PITH INGEST ERROR: file not found: {filepath}]')
        return

    source_content = filepath.read_text(errors='ignore')
    wiki_index     = read_wiki_index(cwd)
    source_title   = filepath.stem.replace('-', ' ').replace('_', ' ').title()
    now            = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    print(f'Analyzing {filepath.name}...')

    # Step 1: Extract information
    analysis_raw = call_claude(INGEST_PROMPT.format(
        source_content=source_content[:8000],  # limit to avoid huge prompts
        wiki_index=wiki_index[:3000],
    ))

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
        page_path = cwd / page_spec['path']
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_type = page_spec.get('type', 'concept')
        fmt = ENTITY_FORMAT if page_type == 'entity' else CONCEPT_FORMAT
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
        page_path.write_text(content)
        print(f'  ✓ {page_spec["path"]}')

    # Step 3: Update index and log
    update_index(cwd, analysis.get('create_pages', []), source_title, rel_source)
    append_log(cwd, source_title, analysis)

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


def main():
    p = argparse.ArgumentParser()
    p.add_argument('filepath')
    args = p.parse_args()
    ingest(Path(args.filepath).resolve())

if __name__ == '__main__':
    main()
