#!/usr/bin/env python3
"""
Pith Hindsight — retrospective context pruning.
Inspired by Hindsight (2026): identify stale tool results consuming context.

Reads session telemetry, finds superseded file reads and large early-session
outputs, reports total stale token cost, recommends /compact.

Usage:
    python3 hindsight.py           # full report
    python3 hindsight.py --nudge   # one-line nudge if worth acting on (else silent)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

STATE   = Path.home() / '.pith' / 'state.json'
TELEM   = Path.home() / '.pith' / 'telemetry.jsonl'
CWD_KEY = 'proj_' + re.sub(r'[^a-zA-Z0-9]', '', __import__('base64').b64encode(
    os.getcwd().encode()).decode())[:20]

BOLD   = '\033[1m'
DIM    = '\033[2m'
RESET  = '\033[0m'
GREEN  = '\033[32m'
YELLOW = '\033[33m'
RED    = '\033[31m'
PURPLE = '\033[38;5;99m'

CONTEXT_LIMIT = 200_000
# Minimum stale % of used context before reporting
NUDGE_THRESHOLD_PCT = 8


def load_proj() -> dict:
    try:
        if STATE.exists():
            d = json.loads(STATE.read_text())
            return d.get(CWD_KEY, {})
    except Exception:
        pass
    return {}


def load_session_telemetry(session_start: str) -> list[dict]:
    if not TELEM.exists():
        return []
    entries = []
    try:
        for line in TELEM.read_text(errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get('session') == session_start:
                    entries.append(e)
            except Exception:
                pass
    except Exception:
        pass
    return entries


def fmt(n: int) -> str:
    if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
    if n >= 1_000:     return f'{n/1_000:.1f}k'
    return str(n)


def analyze(entries: list[dict], used_tokens: int) -> dict:
    """Return stale candidates sorted by token cost."""
    entries = sorted(entries, key=lambda e: e.get('ts', ''))
    total   = len(entries)

    # Superseded reads: same label read more than once — all but the last are stale
    file_reads: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        if e.get('tool', '').lower() in ('read', 'readfile') and e.get('label'):
            file_reads.setdefault(e['label'], []).append(i)

    superseded: set[tuple[str, int]] = set()
    for label, idxs in file_reads.items():
        if len(idxs) > 1:
            for idx in idxs[:-1]:
                superseded.add((label, idx))

    # Early-large: first 60% of session, >80 tokens after compression
    early_cut = max(1, int(total * 0.6))

    stale: list[dict] = []
    seen: set[str]    = set()
    for i, e in enumerate(entries):
        label  = e.get('label', '') or e.get('tool', '')
        tool   = e.get('tool', '').lower()
        toks   = e.get('after_tokens', 0)
        is_sup = (label, i) in superseded
        is_early_large = i < early_cut and toks > 80

        if (is_sup or is_early_large) and toks > 0:
            key = f'{tool}:{label}'
            if key not in seen:
                seen.add(key)
                stale.append({
                    'tool':   tool,
                    'label':  label[:55],
                    'tokens': toks,
                    'reason': 'superseded' if is_sup else 'early/large',
                })

    stale.sort(key=lambda x: x['tokens'], reverse=True)
    total_stale = sum(x['tokens'] for x in stale)
    pct = round(total_stale / used_tokens * 100) if used_tokens else 0
    return {'stale': stale, 'total_stale': total_stale, 'pct': pct}


def report(proj: dict) -> None:
    session_start = proj.get('session_start', '')
    used_tokens   = proj.get('input_tokens_est', 0)
    entries       = load_session_telemetry(session_start)
    total_calls   = len(entries)

    if not entries:
        fill = round(used_tokens / CONTEXT_LIMIT * 100)
        print(f'[PITH HINDSIGHT: no telemetry for current session. '
              f'Context {fill}% full — tool compression may not be active yet.]')
        return

    res   = analyze(entries, used_tokens)
    stale = res['stale']
    total_stale = res['total_stale']
    pct   = res['pct']

    if not stale:
        fill = round(used_tokens / CONTEXT_LIMIT * 100)
        print(f'[PITH HINDSIGHT: no stale results detected. '
              f'Context {fill}% full across {total_calls} tool calls this session.]')
        return

    top = stale[:7]

    print()
    print(f'  {PURPLE}{BOLD}◆ PITH HINDSIGHT{RESET}{DIM} · stale context analysis{RESET}')
    print(f'  {DIM}{"─" * 46}{RESET}')
    print()
    print(f'  {BOLD}{len(stale)} stale results{RESET} ~{fmt(total_stale)} tokens  '
          f'{YELLOW}({pct}% of context){RESET}')
    print()
    print(f'  {DIM}Top prune targets:{RESET}')
    for s in top:
        reason = '← superseded' if s['reason'] == 'superseded' else '← early, large'
        print(f'    {DIM}·{RESET}  {s["label"]:<48} '
              f'{YELLOW}{fmt(s["tokens"])} tok{RESET}  {DIM}{reason}{RESET}')
    if len(stale) > 7:
        remainder = sum(x['tokens'] for x in stale[7:])
        print(f'    {DIM}    … {len(stale) - 7} more ({fmt(remainder)} tok){RESET}')
    print()
    print(f'  {DIM}Recommended action:{RESET} {BOLD}/compact{RESET}  '
          f'{DIM}— summarises history and clears stale context.{RESET}')
    print(f'  {DIM}Estimated recovery: ~{fmt(total_stale)} tokens ({pct}% of context).{RESET}')
    print()


def nudge(proj: dict) -> None:
    """Print a single-line nudge if worth acting on, else print nothing."""
    session_start = proj.get('session_start', '')
    used_tokens   = proj.get('input_tokens_est', 0)
    entries       = load_session_telemetry(session_start)

    if not entries:
        return

    res = analyze(entries, used_tokens)
    if res['pct'] >= NUDGE_THRESHOLD_PCT:
        print(
            f'[PITH HINDSIGHT: {len(res["stale"])} stale tool results '
            f'~{fmt(res["total_stale"])} tokens ({res["pct"]}% of context). '
            f'Run /pith hindsight for details or /compact to clear.]'
        )


def main():
    p = argparse.ArgumentParser(description='Pith Hindsight — stale context analysis')
    p.add_argument('--nudge', action='store_true', help='One-line nudge only')
    args = p.parse_args()
    proj = load_proj()
    if args.nudge:
        nudge(proj)
    else:
        report(proj)


if __name__ == '__main__':
    main()
