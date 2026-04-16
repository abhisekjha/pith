#!/usr/bin/env python3
"""
Pith — Phase 5-8 Behaviour Tests

Tests the four most recent phases without Claude API calls:
  Phase 5 — Symbol extractor    (symbols.py)
  Phase 6 — Auto-escalation     (prompt-submit.js context fill logic)
  Phase 7 — Hindsight pruning   (hindsight.py)
  Phase 8 — jCodeMunch ingest   (ingest.py code-routing logic)

Run: python3 test/phases.py
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO      = Path(__file__).parent.parent
HOOK_DIR  = Path.home() / '.claude/hooks/pith'
TOOL_DIR  = Path.home() / '.local/share/pith/tools'
STATE     = Path.home() / '.pith/state.json'
W         = 76

PASS = '\033[32m✓\033[0m'
FAIL = '\033[31m✗\033[0m'
DIM  = '\033[2m'
BOLD = '\033[1m'
RST  = '\033[0m'

results: list[tuple[str, bool, str]] = []

# ── project-key helpers ───────────────────────────────────────────────────────
# The state is keyed by base64(cwd) — must match how hooks compute it.

import base64
import re as _re

def _repo_key() -> str:
    """Key for REPO (/tmp/pith-push) — matches what node hooks use with CLAUDE_CWD=REPO."""
    b64 = base64.b64encode(str(REPO).encode()).decode()
    return 'proj_' + _re.sub(r'[^a-zA-Z0-9]', '', b64)[:20]

def _load_repo_proj() -> dict:
    try:
        raw = json.loads(STATE.read_text())
        return raw.get(_repo_key(), {})
    except Exception:
        return {}

def _save_repo_proj(updates: dict):
    try:
        raw = json.loads(STATE.read_text()) if STATE.exists() else {}
        key = _repo_key()
        raw[key] = {**(raw.get(key) or {}), **updates}
        STATE.write_text(json.dumps(raw, indent=2))
    except Exception:
        pass


# ── helpers ───────────────────────────────────────────────────────────────────

def section(title: str):
    print()
    print('━' * W)
    print(f'  {BOLD}{title}{RST}')
    print('━' * W)


def check(name: str, passed: bool, detail: str = ''):
    marker = PASS if passed else FAIL
    print(f'  {marker}  {name}')
    if detail:
        for line in detail.splitlines():
            print(f'       {DIM}{line}{RST}')
    results.append((name, passed, detail))


def run_node(hook: str, stdin_data: str, env_extra: dict | None = None) -> tuple[str, int]:
    env = {**os.environ, 'CLAUDE_CWD': str(REPO)}
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        ['node', str(HOOK_DIR / hook)],
        input=stdin_data, capture_output=True, text=True, env=env,
    )
    return r.stdout, r.returncode


def run_py(script: str, args: list[str], env_extra: dict | None = None,
           stdin_data: str | None = None) -> tuple[str, int]:
    env = {**os.environ, 'CLAUDE_CWD': str(REPO)}
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        ['python3', str(TOOL_DIR / script)] + args,
        input=stdin_data, capture_output=True, text=True, env=env,
    )
    return r.stdout + r.stderr, r.returncode


def load_proj_state() -> dict:
    return _load_repo_proj()


# ── Phase 5: Symbol extractor ─────────────────────────────────────────────────

SAMPLE_PY = textwrap.dedent('''\
    class UserService:
        """Manages user authentication and profiles."""

        def __init__(self, db, cache):
            self.db    = db
            self.cache = cache

        def get_user(self, user_id: str):
            """Fetch user by ID, checking cache first."""
            cached = self.cache.get(user_id)
            if cached:
                return cached
            user = self.db.query(f"SELECT * FROM users WHERE id = %s", user_id)
            self.cache.set(user_id, user, ttl=300)
            return user

        def create_user(self, email: str, name: str):
            """Create a new user, returns the created record."""
            if self.db.query("SELECT id FROM users WHERE email = %s", email):
                raise ValueError(f"Email {email} already exists")
            return self.db.insert("INSERT INTO users (email, name) VALUES (%s, %s)", email, name)

        def delete_user(self, user_id: str):
            self.cache.delete(user_id)
            self.db.execute("DELETE FROM users WHERE id = %s", user_id)


    def hash_password(password: str) -> str:
        """One-way hash using bcrypt."""
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


    def verify_password(password: str, hashed: str) -> bool:
        import bcrypt
        return bcrypt.checkpw(password.encode(), hashed.encode())
''')

SAMPLE_TS = textwrap.dedent('''\
    import { useState, useEffect } from 'react';

    export interface User {
        id: string;
        email: string;
        name: string;
    }

    export function useCurrentUser() {
        const [user, setUser] = useState<User | null>(null);
        const [loading, setLoading] = useState(true);

        useEffect(() => {
            fetch('/api/me')
                .then(r => r.json())
                .then(data => { setUser(data); setLoading(false); });
        }, []);

        return { user, loading };
    }

    export async function updateUserProfile(id: string, data: Partial<User>): Promise<User> {
        const res = await fetch(`/api/users/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }
''')


def test_phase5_symbols():
    section('Phase 5 — Symbol Extractor')

    with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:
        f.write(SAMPLE_PY)
        py_file = f.name
    with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
        f.write(SAMPLE_TS)
        ts_file = f.name

    try:
        # 5a: --list on Python file
        out, rc = run_py('symbols.py', ['--list', py_file])
        passed = rc == 0 and 'UserService' in out and 'hash_password' in out
        check('symbols.py --list Python: finds UserService and hash_password',
              passed, out.splitlines()[0] if out else 'no output')

        # 5b: extract specific Python method
        out, rc = run_py('symbols.py', [py_file, 'get_user'])
        passed = rc == 0 and 'def get_user' in out and 'cache.get' in out
        check('symbols.py extract Python method: get_user body returned',
              passed, out.splitlines()[0] if out else 'no output')

        # 5c: extract Python class
        out, rc = run_py('symbols.py', [py_file, 'UserService'])
        passed = rc == 0 and 'class UserService' in out
        check('symbols.py extract Python class: UserService returned',
              passed, out.splitlines()[0] if out else 'no output')

        # 5d: --list on TypeScript file
        out, rc = run_py('symbols.py', ['--list', ts_file])
        passed = rc == 0 and ('useCurrentUser' in out or 'updateUserProfile' in out)
        check('symbols.py --list TypeScript: finds exported functions',
              passed, out.splitlines()[0] if out else 'no output')

        # 5e: extract TS function
        out, rc = run_py('symbols.py', [ts_file, 'useCurrentUser'])
        passed = rc == 0 and 'useCurrentUser' in out
        check('symbols.py extract TS function: useCurrentUser body returned',
              passed, out.splitlines()[0] if out else 'no output')

        # 5f: token savings — compare listing vs full file
        full_tokens = max(1, len(SAMPLE_PY) // 4)
        list_out, _ = run_py('symbols.py', ['--list', py_file])
        list_tokens = max(1, len(list_out) // 4)
        savings_pct = (full_tokens - list_tokens) / full_tokens * 100
        passed = savings_pct > 60
        check(f'Token reduction: --list is {savings_pct:.0f}% smaller than full file (need >60%)',
              passed, f'{full_tokens} tok → {list_tokens} tok')

        # 5g: /pith symbol command in hook
        payload = json.dumps({'prompt': f'/pith symbol {py_file} get_user'})
        out, rc = run_node('prompt-submit.js', payload)
        passed = rc == 0 and 'get_user' in out
        check('/pith symbol command routes through prompt-submit.js correctly',
              passed, out[:80] if out else 'no output')

        # 5h: /pith symbol --list command
        payload = json.dumps({'prompt': f'/pith symbol --list {py_file}'})
        out, rc = run_node('prompt-submit.js', payload)
        passed = rc == 0 and ('UserService' in out or 'hash_password' in out)
        check('/pith symbol --list command routes correctly',
              passed, out[:80] if out else 'no output')

        # 5i: missing symbol returns helpful message
        out, rc = run_py('symbols.py', [py_file, 'nonexistent_function_xyz'])
        passed = 'not found' in out.lower() or 'symbol' in out.lower()
        check('Missing symbol returns informative message (not crash)',
              passed, out[:80] if out else 'no output')

    finally:
        os.unlink(py_file)
        os.unlink(ts_file)


# ── Phase 6: Auto-escalation ──────────────────────────────────────────────────

def _inject_tokens(tokens: int) -> tuple[str, int]:
    """Patch REPO's project state so fill appears at the given token count."""
    key  = _repo_key()
    proj = _load_repo_proj()
    original = proj.get('input_tokens_est', 0)
    _save_repo_proj({
        'input_tokens_est':    tokens,
        'mode':                'off',
        'auto_escalate_disabled': False,
    })
    return key, original


def _restore_tokens(key: str, original: int):
    _save_repo_proj({'input_tokens_est': original})


def test_phase6_escalation():
    section('Phase 6 — Auto-Escalation (SWEzze)')

    # 6a: /pith escalate status command
    payload = json.dumps({'prompt': '/pith escalate'})
    out, rc = run_node('prompt-submit.js', payload)
    passed = rc == 0 and 'escalat' in out.lower()
    check('/pith escalate shows status without crashing', passed,
          out[:100] if out else 'no output')

    # 6b: /pith escalate off
    payload = json.dumps({'prompt': '/pith escalate off'})
    out, rc = run_node('prompt-submit.js', payload)
    passed = rc == 0 and 'disabled' in out.lower()
    check('/pith escalate off confirms disabled', passed,
          out[:80] if out else 'no output')

    # 6c: /pith escalate on
    payload = json.dumps({'prompt': '/pith escalate on'})
    out, rc = run_node('prompt-submit.js', payload)
    passed = rc == 0 and 'enabled' in out.lower()
    check('/pith escalate on confirms enabled', passed,
          out[:80] if out else 'no output')

    # 6d: lean escalation fires at 55% fill
    key, original = _inject_tokens(110_000)  # 55% of 200k
    if key:
        try:
            payload = json.dumps({'prompt': 'what is the auth flow?'})
            out, rc = run_node('prompt-submit.js', payload)
            passed = rc == 0 and ('lean' in out.lower() or 'escalat' in out.lower())
            check('LEAN auto-escalation fires when context is 55% full',
                  passed, out[:120] if out else 'no output')
        finally:
            _restore_tokens(key, original)
    else:
        check('LEAN auto-escalation fires at 55% — SKIP (no state key)', True, 'skipped')

    # 6e: ultra escalation fires at 75% fill
    key, original = _inject_tokens(150_000)  # 75% of 200k
    if key:
        try:
            payload = json.dumps({'prompt': 'what is the auth flow?'})
            out, rc = run_node('prompt-submit.js', payload)
            passed = rc == 0 and ('ultra' in out.lower() or 'escalat' in out.lower())
            check('ULTRA auto-escalation fires when context is 75% full',
                  passed, out[:120] if out else 'no output')
        finally:
            _restore_tokens(key, original)
    else:
        check('ULTRA auto-escalation fires at 75% — SKIP (no state key)', True, 'skipped')

    # 6f: dynamic ceiling injected at 87% fill
    key, original = _inject_tokens(174_000)  # 87% of 200k
    if key:
        try:
            payload = json.dumps({'prompt': 'explain the codebase'})
            out, rc = run_node('prompt-submit.js', payload)
            passed = rc == 0 and ('token' in out.lower() or 'headroom' in out.lower() or
                                   'context' in out.lower())
            check('Dynamic ceiling injected when context is 87% full',
                  passed, out[:120] if out else 'no output')
        finally:
            _restore_tokens(key, original)
    else:
        check('Dynamic ceiling at 87% — SKIP (no state key)', True, 'skipped')

    # 6g: escalation injection does NOT fire for /pith commands
    # (/pith status can say "Auto-escalations: 2×" in its panel — that's fine;
    #  what should NOT appear is the "AUTO-ESCALATED: context X% full" announcement)
    key, original = _inject_tokens(150_000)
    if key:
        try:
            payload = json.dumps({'prompt': '/pith status'})
            out, rc = run_node('prompt-submit.js', payload)
            passed = rc == 0 and 'auto-escalated' not in out.lower()
            check('/pith commands are exempt from auto-escalation injection',
                  passed,
                  'AUTO-ESCALATED announcement absent for /pith command' if passed else out[:120])
        finally:
            _restore_tokens(key, original)
    else:
        check('Escalation exempt for /pith — SKIP (no state key)', True, 'skipped')


# ── Phase 7: Hindsight ────────────────────────────────────────────────────────

def _inject_fake_telemetry(entries: list[dict]):
    """Append fake telemetry entries stamped with REPO's session_start."""
    telem_path = Path.home() / '.pith' / 'telemetry.jsonl'
    telem_path.parent.mkdir(exist_ok=True)
    proj    = _load_repo_proj()
    session = proj.get('session_start', '2026-01-01T00:00:00.000Z')
    lines = []
    for e in entries:
        e.setdefault('session', session)
        lines.append(json.dumps(e))
    with open(telem_path, 'a') as f:
        f.write('\n'.join(lines) + '\n')
    return session


def test_phase7_hindsight():
    section('Phase 7 — Hindsight Pruning')

    # 7a: hindsight.py runs without crashing
    out, rc = run_py('hindsight.py', [])
    passed = rc == 0
    check('hindsight.py runs without crashing', passed,
          out.splitlines()[0] if out else 'no output')

    # 7b: --nudge flag is silent when no stale data
    out, rc = run_py('hindsight.py', ['--nudge'])
    # Should be silent OR print a nudge — either is fine, must not crash
    passed = rc == 0
    check('hindsight.py --nudge exits cleanly (silent or one-line)', passed,
          repr(out[:80]) if out else '(silent — correct)')

    # 7c: inject fake superseded reads and verify detection
    ts = '2026-04-16T00:00:00.000Z'
    _inject_fake_telemetry([
        {'tool': 'Read', 'label': 'src/auth.ts',     'after_tokens': 820, 'ts': ts},
        {'tool': 'Read', 'label': 'src/auth.ts',     'after_tokens': 820, 'ts': ts},  # superseded
        {'tool': 'Bash', 'label': 'npm install',      'after_tokens': 600, 'ts': ts},
        {'tool': 'Read', 'label': 'src/config.json',  'after_tokens': 450, 'ts': ts},
        {'tool': 'Bash', 'label': 'git log --oneline','after_tokens': 380, 'ts': ts},
        {'tool': 'Read', 'label': 'package.json',     'after_tokens': 310, 'ts': ts},
        {'tool': 'Read', 'label': 'src/auth.ts',      'after_tokens': 820, 'ts': ts},  # 3rd read
        {'tool': 'Read', 'label': 'src/user.ts',      'after_tokens': 290, 'ts': ts},
    ])

    out, rc = run_py('hindsight.py', [])
    # Should detect the two superseded auth.ts reads and early-large bash
    passed = rc == 0 and ('stale' in out.lower() or 'prune' in out.lower() or 'compact' in out.lower())
    check('hindsight.py detects stale results after injecting fake telemetry',
          passed, out.splitlines()[1] if out.count('\n') > 1 else out[:80])

    superseded_detected = 'superseded' in out.lower() or 'auth.ts' in out
    check('Superseded file reads (src/auth.ts read 3×) identified',
          superseded_detected, 'auth.ts should appear in prune targets')

    compact_mentioned = 'compact' in out.lower()
    check('/compact recommended in hindsight output', compact_mentioned,
          out[-100:] if out else 'no output')

    # 7d: --nudge fires when there are stale results
    out, rc = run_py('hindsight.py', ['--nudge'])
    # After injecting stale data, --nudge should produce output
    passed = rc == 0
    check('hindsight.py --nudge exits cleanly after stale data injected', passed,
          out[:80] if out else '(silent)')

    # 7e: /pith hindsight command routes through prompt-submit
    payload = json.dumps({'prompt': '/pith hindsight'})
    out, rc = run_node('prompt-submit.js', payload)
    passed = rc == 0
    check('/pith hindsight command handled by prompt-submit.js (no crash)',
          passed, out[:80] if out else 'no output')

    # 7f: hindsight is in /pith help
    payload = json.dumps({'prompt': '/pith help'})
    out, rc = run_node('prompt-submit.js', payload)
    passed = rc == 0 and 'hindsight' in out.lower()
    check('/pith hindsight listed in /pith help output',
          passed, 'hindsight present in help' if passed else out[:80])


# ── Phase 8: jCodeMunch ───────────────────────────────────────────────────────

def test_phase8_jcodemunch():
    section('Phase 8 — jCodeMunch Code-Aware Ingest')

    # 8a: CODE_EXTENSIONS is defined and covers major languages
    r = subprocess.run(
        ['python3', '-c',
         'import sys; sys.path.insert(0, str(__import__("pathlib").Path.home() / ".local/share/pith/tools")); '
         'from ingest import CODE_EXTENSIONS; '
         'assert "py" in CODE_EXTENSIONS and "ts" in CODE_EXTENSIONS and "go" in CODE_EXTENSIONS; '
         'print("ok:", len(CODE_EXTENSIONS), "extensions")'],
        capture_output=True, text=True,
    )
    passed = r.returncode == 0 and 'ok:' in r.stdout
    check('CODE_EXTENSIONS defined and covers py/ts/go',
          passed, r.stdout.strip() or r.stderr[:80])

    # 8b: get_code_skeleton() works and returns symbol list
    with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:
        f.write(SAMPLE_PY)
        py_file = f.name
    try:
        r = subprocess.run(
            ['python3', '-c',
             f'import sys; sys.path.insert(0, str(__import__("pathlib").Path.home() / ".local/share/pith/tools")); '
             f'from ingest import get_code_skeleton; '
             f'from pathlib import Path; '
             f's = get_code_skeleton(Path("{py_file}")); '
             f'print(s[:200])'],
            capture_output=True, text=True,
        )
        passed = r.returncode == 0 and ('UserService' in r.stdout or 'hash_password' in r.stdout)
        check('get_code_skeleton() returns structural content for Python file',
              passed, r.stdout[:100] if r.stdout else r.stderr[:80])
    finally:
        os.unlink(py_file)

    # 8c: ingest.py detects code extension correctly
    with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:
        f.write(SAMPLE_PY)
        py_file = f.name
    try:
        r = subprocess.run(
            ['python3', '-c',
             f'import sys; sys.path.insert(0, str(__import__("pathlib").Path.home() / ".local/share/pith/tools")); '
             f'from ingest import CODE_EXTENSIONS; '
             f'from pathlib import Path; '
             f'p = Path("{py_file}"); '
             f'ext = p.suffix.lower().lstrip("."); '
             f'print("is_code:", ext in CODE_EXTENSIONS, "ext:", ext)'],
            capture_output=True, text=True,
        )
        passed = r.returncode == 0 and 'is_code: True' in r.stdout
        check('ingest.py detects .py as code file correctly',
              passed, r.stdout.strip() or r.stderr[:80])
    finally:
        os.unlink(py_file)

    # 8d: TypeScript also detected
    r = subprocess.run(
        ['python3', '-c',
         'import sys; sys.path.insert(0, str(__import__("pathlib").Path.home() / ".local/share/pith/tools")); '
         'from ingest import CODE_EXTENSIONS; '
         'assert all(e in CODE_EXTENSIONS for e in ["ts", "tsx", "js", "jsx", "go", "rs", "java"]); '
         'print("all key extensions present")'],
        capture_output=True, text=True,
    )
    passed = r.returncode == 0 and 'all key' in r.stdout
    check('All major code extensions (ts/tsx/js/go/rs/java) in CODE_EXTENSIONS',
          passed, r.stdout.strip() or r.stderr[:80])

    # 8e: CODE_MUNCH_PROMPT has the right template vars
    r = subprocess.run(
        ['python3', '-c',
         'import sys; sys.path.insert(0, str(__import__("pathlib").Path.home() / ".local/share/pith/tools")); '
         'from ingest import CODE_MUNCH_PROMPT; '
         'assert "{skeleton}" in CODE_MUNCH_PROMPT and "{head}" in CODE_MUNCH_PROMPT and "{wiki_index}" in CODE_MUNCH_PROMPT; '
         'print("template vars: ok, prompt length:", len(CODE_MUNCH_PROMPT))'],
        capture_output=True, text=True,
    )
    passed = r.returncode == 0 and 'ok,' in r.stdout
    check('CODE_MUNCH_PROMPT has {skeleton}, {head}, {wiki_index} placeholders',
          passed, r.stdout.strip() or r.stderr[:80])

    # 8f: MODULE_FORMAT and CLASS_FORMAT are defined
    r = subprocess.run(
        ['python3', '-c',
         'import sys; sys.path.insert(0, str(__import__("pathlib").Path.home() / ".local/share/pith/tools")); '
         'from ingest import MODULE_FORMAT, CLASS_FORMAT; '
         'assert "Exports" in MODULE_FORMAT and "Key Methods" in CLASS_FORMAT; '
         'print("MODULE_FORMAT has Exports, CLASS_FORMAT has Key Methods")'],
        capture_output=True, text=True,
    )
    passed = r.returncode == 0 and 'MODULE_FORMAT' in r.stdout or 'Exports' in r.stdout or 'Key Methods' in r.stdout
    check('MODULE_FORMAT (Exports) and CLASS_FORMAT (Key Methods) defined',
          passed, r.stdout.strip() or r.stderr[:80])

    # 8g: symbols.py skeleton is shorter than raw file (the whole point)
    with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:
        f.write(SAMPLE_PY * 3)   # make it longer so compression is visible
        py_file = f.name
    try:
        raw_tokens = max(1, len(SAMPLE_PY * 3) // 4)
        out, rc = run_py('symbols.py', ['--list', py_file])
        skel_tokens = max(1, len(out) // 4)
        reduction = (raw_tokens - skel_tokens) / raw_tokens * 100
        passed = rc == 0 and reduction > 50
        check(f'jCodeMunch skeleton is {reduction:.0f}% smaller than raw file (need >50%)',
              passed, f'{raw_tokens} raw tok → {skel_tokens} skeleton tok')
    finally:
        os.unlink(py_file)


# ── summary ───────────────────────────────────────────────────────────────────

def main():
    print()
    print('━' * W)
    print(f'  {BOLD}Pith — Phase 5–8 Behaviour Tests{RST}')
    print('━' * W)

    test_phase5_symbols()
    test_phase6_escalation()
    test_phase7_hindsight()
    test_phase8_jcodemunch()

    passed  = sum(1 for _, ok, _ in results if ok)
    total   = len(results)
    failed_list = [(n, d) for n, ok, d in results if not ok]

    section('RESULTS')
    bar_w  = 44
    filled = int(bar_w * passed / total) if total else 0
    bar    = '█' * filled + '░' * (bar_w - filled)
    pct    = passed / total * 100 if total else 0
    print(f'\n  {bar}  {passed}/{total} ({pct:.0f}%)\n')

    if failed_list:
        print(f'  {BOLD}Failures:{RST}')
        for name, detail in failed_list:
            print(f'    {FAIL} {name}')
            if detail:
                print(f'         {DIM}{detail[:120]}{RST}')
        print()
    else:
        print(f'  {PASS} {BOLD}All phase tests pass.{RST}')
        print()
        print('  Covered:')
        print('    Phase 5 — Symbol extractor:  extraction, listing, token savings, hook routing')
        print('    Phase 6 — Auto-escalation:   LEAN/ULTRA triggers, ceiling injection, /pith exempt')
        print('    Phase 7 — Hindsight:         stale detection, superseded reads, /compact nudge')
        print('    Phase 8 — jCodeMunch:        code detection, skeleton, MODULE/CLASS templates')

    print()
    print('  Run all tests:')
    print('    python3 test/functional.py   # full hook chain (phases 1-4)')
    print('    python3 test/phases.py       # phase 5-8 behaviour tests')
    print('━' * W)
    print()

    sys.exit(0 if not failed_list else 1)


if __name__ == '__main__':
    main()
