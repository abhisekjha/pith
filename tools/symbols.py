#!/usr/bin/env python3
"""
Pith Symbol Lookup — return exact source lines for a named symbol.

Instead of reading a whole file (expensive), call this to get only the
30-50 lines of the specific function, class, or method you need.

Token reduction: ~95% vs reading the full file (30 lines vs 1,000).

Usage:
    python3 symbols.py <file_path> <symbol_name>
    python3 symbols.py src/auth.ts handleLogin
    python3 symbols.py --list src/auth.ts          # list all symbols in file

Supports: Python, JS/TS/JSX/TSX, Go, Java/Kotlin/Swift/C#/Rust (brace-counting)
Upgrade: install tree-sitter-languages for exact AST-based extraction.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

# ── Mtime cache ───────────────────────────────────────────────────────────────

_SYM_CACHE_FILE = Path('/tmp/pith-symbols-cache.json')
_SYM_CACHE_MAX  = 500

def _load_sym_cache() -> dict:
    try:
        return json.loads(_SYM_CACHE_FILE.read_text()) if _SYM_CACHE_FILE.exists() else {}
    except Exception:
        return {}

def _save_sym_cache(cache: dict) -> None:
    try:
        if len(cache) > _SYM_CACHE_MAX:
            cache = dict(list(cache.items())[-_SYM_CACHE_MAX:])
        tmp = _SYM_CACHE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(cache))
        tmp.replace(_SYM_CACHE_FILE)
    except Exception:
        pass

def _sym_key(path: Path, symbol: str) -> str:
    return f'{path.resolve()}:{path.stat().st_mtime}:{symbol}'


# ── Tree-sitter (preferred if installed) ─────────────────────────────────────

def _try_treesitter(file_path: Path, symbol_name: str) -> str | None:
    """Use tree-sitter-languages for precise AST extraction if available."""
    try:
        from tree_sitter_languages import get_language, get_parser  # type: ignore
        ext = file_path.suffix.lower().lstrip('.')
        lang_map = {
            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
            'jsx': 'javascript', 'tsx': 'tsx', 'go': 'go',
            'java': 'java', 'kt': 'kotlin', 'rs': 'rust', 'cs': 'c_sharp',
        }
        lang_name = lang_map.get(ext)
        if not lang_name:
            return None

        parser   = get_parser(lang_name)
        language = get_language(lang_name)
        source   = file_path.read_bytes()
        tree     = parser.parse(source)

        # Query for named function/class/method definitions
        queries = {
            'python':     '[(function_definition name: (identifier) @name)'
                          ' (class_definition name: (identifier) @name)] @def',
            'javascript': '[(function_declaration name: (identifier) @name)'
                          ' (class_declaration name: (identifier) @name)'
                          ' (method_definition name: (property_identifier) @name)'
                          ' (variable_declarator name: (identifier) @name)] @def',
            'typescript': '[(function_declaration name: (identifier) @name)'
                          ' (class_declaration name: (identifier) @name)'
                          ' (method_definition name: (property_identifier) @name)'
                          ' (variable_declarator name: (identifier) @name)] @def',
            'go':         '[(function_declaration name: (identifier) @name)'
                          ' (method_declaration name: (field_identifier) @name)] @def',
        }
        q_str = queries.get(lang_name)
        if not q_str:
            return None

        query   = language.query(q_str)
        matches = query.matches(tree.root_node)
        lines   = source.decode('utf-8', errors='replace').split('\n')

        for _, captures in matches:
            name_nodes = captures.get('name', [])
            def_nodes  = captures.get('def',  [])
            if not name_nodes or not def_nodes:
                continue
            name_node = name_nodes[0] if isinstance(name_nodes, list) else name_nodes
            def_node  = def_nodes[0]  if isinstance(def_nodes,  list) else def_nodes
            if name_node.text.decode() == symbol_name:
                start = def_node.start_point[0]
                end   = def_node.end_point[0] + 1
                block = lines[start:end]
                return (
                    f'[PITH SYMBOL: {symbol_name} in {file_path.name} '
                    f'(lines {start+1}–{end}, tree-sitter)]\n\n' +
                    '\n'.join(f'{start+1+i:4}  {l}' for i, l in enumerate(block))
                )
        return f'[PITH: symbol "{symbol_name}" not found in {file_path.name} (tree-sitter)]'

    except ImportError:
        return None   # fall through to regex
    except Exception:
        return None


# ── Regex fallback ────────────────────────────────────────────────────────────

def _find_start(lines: list[str], symbol: str, ext: str) -> int | None:
    """Return 0-based index of the line where symbol definition starts."""
    patterns: list[str] = []

    if ext == 'py':
        patterns = [
            rf'^\s*(async\s+)?def\s+{re.escape(symbol)}\s*[\(:]',
            rf'^\s*class\s+{re.escape(symbol)}\s*[:\(]',
        ]
    elif ext in ('js', 'ts', 'jsx', 'tsx', 'mjs', 'cjs'):
        patterns = [
            rf'^\s*(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*{re.escape(symbol)}\s*[\(<]',
            rf'^\s*(export\s+)?(const|let|var)\s+{re.escape(symbol)}\s*=\s*(async\s+)?(\(|function)',
            rf'^\s*(abstract\s+)?class\s+{re.escape(symbol)}\s*[{{\(<]',
            rf'^\s*(async\s+)?(static\s+)?(private\s+|public\s+|protected\s+)?{re.escape(symbol)}\s*[\(<]',
            rf'^\s*(async\s+)?{re.escape(symbol)}\s*[:=]\s*(async\s+)?\(',
        ]
    elif ext == 'go':
        patterns = [
            rf'^\s*func\s+(\(\w+\s+\*?\w+\)\s+)?{re.escape(symbol)}\s*[\(<]',
        ]
    else:   # generic brace-counting languages
        patterns = [
            rf'^\s*(public|private|protected|static|async|override|abstract|\s)*'
            rf'[\w<>\[\]]+\s+{re.escape(symbol)}\s*[\(<{{]',
            rf'^\s*(abstract\s+)?class\s+{re.escape(symbol)}\s*[{{\(<]',
        ]

    compiled = [re.compile(p) for p in patterns]
    for i, line in enumerate(lines):
        if any(p.match(line) for p in compiled):
            return i
    return None


def _extract_block_brace(lines: list[str], start: int) -> list[str]:
    """Extract a brace-delimited block starting at `start`."""
    depth   = 0
    started = False
    block   = []
    for line in lines[start:]:
        block.append(line)
        opens  = line.count('{')
        closes = line.count('}')
        if opens > 0:
            started = True
        depth += opens - closes
        if started and depth <= 0:
            break
        if len(block) > 300:    # safety cap
            break
    return block


def _extract_block_indent(lines: list[str], start: int) -> list[str]:
    """Extract an indentation-delimited block (Python) starting at `start`."""
    header   = lines[start]
    base_ind = len(header) - len(header.lstrip())
    block    = [header]
    for line in lines[start + 1:]:
        if not line.strip():                        # blank lines kept
            block.append(line)
            continue
        ind = len(line) - len(line.lstrip())
        if ind <= base_ind and line.strip():        # back to same/lower indent
            break
        block.append(line)
        if len(block) > 300:
            break
    # Strip trailing blanks
    while block and not block[-1].strip():
        block.pop()
    return block


_PY_KEYWORDS = {
    'if', 'else', 'elif', 'for', 'while', 'try', 'except', 'finally', 'with',
    'return', 'yield', 'import', 'from', 'class', 'def', 'pass', 'break',
    'continue', 'and', 'or', 'not', 'in', 'is', 'lambda', 'raise', 'assert',
    'del', 'global', 'nonlocal', 'print', 'len', 'range', 'type', 'str', 'int',
    'float', 'list', 'dict', 'set', 'tuple', 'bool', 'None', 'True', 'False',
    'super', 'self', 'cls', 'open', 'enumerate', 'zip', 'map', 'filter',
}
_JS_KEYWORDS = {
    'if', 'else', 'for', 'while', 'do', 'try', 'catch', 'finally', 'switch',
    'case', 'return', 'yield', 'throw', 'new', 'delete', 'typeof', 'instanceof',
    'const', 'let', 'var', 'function', 'class', 'import', 'export', 'from',
    'await', 'async', 'this', 'super', 'true', 'false', 'null', 'undefined',
    'console', 'require', 'module', 'exports', 'Promise', 'Array', 'Object',
    'String', 'Number', 'Boolean', 'Math', 'JSON', 'Error', 'Set', 'Map',
}


def _speculative_callees(all_lines: list[str], body_lines: list[str],
                         ext: str, symbol_name: str) -> list[str]:
    """
    Phase 10 — Speculative Fetch.
    Scan the extracted body for function calls that exist as symbols in the
    same file. Return their signatures so Claude doesn't need a follow-up turn.
    Capped at 5 callees and skips the symbol itself.
    """
    keywords = _PY_KEYWORDS if ext == 'py' else _JS_KEYWORDS
    calls: set[str] = set()
    for line in body_lines:
        for m in re.finditer(r'\b([a-zA-Z_]\w+)\s*\(', line):
            name = m.group(1)
            if (name not in keywords
                    and name != symbol_name
                    and not name[0].isupper()   # skip class instantiations
                    and len(name) > 2):
                calls.add(name)

    if not calls:
        return []

    found: list[tuple[int, str]] = []
    seen:  set[str]              = set()
    for name in sorted(calls):
        if name in seen:
            continue
        start = _find_start(all_lines, name, ext)
        if start is not None:
            sig = all_lines[start].rstrip()
            # For Python, grab first docstring line too
            if ext == 'py' and start + 1 < len(all_lines):
                nxt = all_lines[start + 1].strip()
                if nxt.startswith('"""') or nxt.startswith("'''"):
                    sig += f'  # {nxt.strip(chr(34)).strip(chr(39)).strip()[:60]}'
            found.append((start + 1, sig))
            seen.add(name)
        if len(found) >= 5:
            break

    return [f'  {ln:4}  {sig}' for ln, sig in found]


def find_symbol(file_path: str, symbol_name: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return f'[PITH: file not found: {file_path}]'

    cache = _load_sym_cache()
    key   = _sym_key(path, symbol_name)
    if key in cache:
        return cache[key]

    # Try tree-sitter first
    ts_result = _try_treesitter(path, symbol_name)
    if ts_result is not None:
        cache[key] = ts_result
        _save_sym_cache(cache)
        return ts_result

    # Regex fallback
    content = path.read_text(errors='ignore')
    lines   = content.split('\n')
    ext     = path.suffix.lower().lstrip('.')

    start = _find_start(lines, symbol_name, ext)
    if start is None:
        # Try case-insensitive as fallback hint
        lower_sym = symbol_name.lower()
        candidates = [
            l.strip() for l in lines
            if lower_sym in l.lower() and re.search(
                r'\b(def|function|class|func|fn)\b', l)
        ]
        hint = f'\n  Did you mean one of: {candidates[:5]}' if candidates else ''
        return f'[PITH: symbol "{symbol_name}" not found in {path.name} (regex){hint}]'

    if ext == 'py':
        block = _extract_block_indent(lines, start)
    else:
        block = _extract_block_brace(lines, start)

    end_line = start + len(block)
    numbered = '\n'.join(f'{start+1+i:4}  {l}' for i, l in enumerate(block))

    # Phase 10 — Speculative Fetch: append called-symbol signatures
    callees = _speculative_callees(lines, block, ext, symbol_name)
    speculative = ''
    if callees:
        speculative = (
            f'\n\n[PITH SPECULATIVE — functions called in {symbol_name} (same file):]\n'
            + '\n'.join(callees)
        )

    result = (
        f'[PITH SYMBOL: {symbol_name} in {path.name} '
        f'(lines {start+1}–{end_line}, regex)]\n\n'
        + numbered
        + speculative
        + f'\n\n[Full file: {len(lines)} lines — /pith symbol --list {file_path} to see all]'
    )
    cache[key] = result
    _save_sym_cache(cache)
    return result


def list_symbols(file_path: str) -> str:
    """List all top-level symbols in a file."""
    path = Path(file_path)
    if not path.exists():
        return f'[PITH: file not found: {file_path}]'

    cache = _load_sym_cache()
    key   = _sym_key(path, '__list__')
    if key in cache:
        return cache[key]

    content = path.read_text(errors='ignore')
    lines   = content.split('\n')
    ext     = path.suffix.lower().lstrip('.')
    found   = []

    if ext == 'py':
        for i, line in enumerate(lines):
            m = re.match(r'^(async\s+)?def\s+(\w+)|^class\s+(\w+)', line)
            if m:
                name = m.group(2) or m.group(3)
                kind = 'class' if line.lstrip().startswith('class') else 'def'
                found.append((i + 1, kind, name))
    elif ext in ('js', 'ts', 'jsx', 'tsx', 'mjs'):
        for i, line in enumerate(lines):
            m = (re.match(r'^\s*(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*(\w+)', line) or
                 re.match(r'^\s*(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?(\(|function)', line) or
                 re.match(r'^\s*(export\s+)?(abstract\s+)?class\s+(\w+)', line))
            if m:
                name = next((g for g in reversed(m.groups()) if g and re.match(r'^\w+$', g)), None)
                kind = 'class' if 'class' in line else 'fn'
                if name:
                    found.append((i + 1, kind, name))
    elif ext == 'go':
        for i, line in enumerate(lines):
            m = re.match(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)', line)
            if m:
                found.append((i + 1, 'func', m.group(1)))

    if not found:
        result = f'[PITH: no symbols found in {path.name} — unsupported language or empty file]'
        cache[key] = result
        _save_sym_cache(cache)
        return result

    rows   = '\n'.join(f'  {kind:6} {name:<40} line {ln}' for ln, kind, name in found)
    result = (
        f'[PITH SYMBOLS: {path.name} — {len(found)} symbols]\n\n'
        + rows +
        f'\n\nUse: /pith symbol {file_path} <name>  to extract any symbol'
    )
    cache[key] = result
    _save_sym_cache(cache)
    return result


def main():
    p = argparse.ArgumentParser(description='Pith symbol extractor')
    p.add_argument('file',   help='Source file path')
    p.add_argument('symbol', nargs='?', help='Symbol name to extract')
    p.add_argument('--list', action='store_true', help='List all symbols in file')
    args = p.parse_args()

    if args.list or not args.symbol:
        print(list_symbols(args.file))
    else:
        print(find_symbol(args.file, args.symbol))


if __name__ == '__main__':
    main()
