#!/usr/bin/env python3
"""
Pith Health — token usage breakdown for the current session.
Called by /pith status.
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path

STATE   = Path.home() / '.pith' / 'state.json'
CWD_KEY = 'proj_' + re.sub(r'[^a-zA-Z0-9]', '', __import__('base64').b64encode(
    os.getcwd().encode()).decode())[:20]

# ANSI helpers
DIM    = '\033[2m'
RESET  = '\033[0m'
BOLD   = '\033[1m'
WHITE  = '\033[97m'
GREEN  = '\033[32m'
YELLOW = '\033[33m'
RED    = '\033[31m'
PURPLE = '\033[38;5;99m'


def load() -> dict:
    try:
        if STATE.exists():
            d = json.loads(STATE.read_text())
            return d.get(CWD_KEY, {})
    except Exception:
        pass
    return {}


def bar(frac: float, w: int = 20) -> str:
    filled = min(round(frac * w), w)
    blocks = '█' * filled + '░' * (w - filled)
    if frac > 0.85:
        return f'{RED}{blocks}{RESET}'
    if frac > 0.70:
        return f'{YELLOW}{blocks}{RESET}'
    return f'{DIM}{blocks}{RESET}'


def fmt(n: int) -> str:
    if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
    if n >= 1_000:     return f'{n/1_000:.1f}k'
    return str(n)


def row(label: str, value: str, color: str = '') -> str:
    """Render a label/value row: label left-aligned, value right-aligned in 8 chars."""
    val = f'{color}{value}{RESET}' if color else value
    return f'  {DIM}{label:<16}{RESET}{val:>8}'


# Pricing (per 1M tokens) — update if model changes
IN_COST_PER_M  = 3.0   # Sonnet 4.6 input
OUT_COST_PER_M = 15.0  # Sonnet 4.6 output


def cost(tokens: int, per_m: float) -> float:
    return tokens / 1_000_000 * per_m


def fmt_cost(c: float) -> str:
    if c < 0.0001:
        return '<$0.0001'
    return f'${c:.4f}'


def main():
    s = load()
    inp     = s.get('input_tokens_est', 0)
    out_tok = s.get('output_tokens_est', 0)
    t_saved   = s.get('tokens_saved_session', 0)
    tool_s    = s.get('tool_savings_session', 0)
    toon_s    = s.get('toon_savings_session', 0)
    skel_s    = s.get('skeleton_savings_session', 0)
    bash_s    = s.get('bash_savings_session', 0)
    out_s     = s.get('output_savings_session', 0)
    compact   = s.get('compact_count_session', 0)
    limit     = s.get('context_limit', 200_000)
    mode      = s.get('mode', 'off')
    budget    = s.get('budget')
    total     = s.get('tokens_saved_total', 0)
    toon_total= s.get('toon_savings_total', 0)
    total_cost_saved = s.get('cost_saved_total', 0.0)

    fill    = inp / limit if limit else 0
    w_total = inp + t_saved
    pct     = round(t_saved / w_total * 100) if w_total else 0
    without = inp + t_saved

    # Cost calculations
    actual_in_cost  = cost(inp,     IN_COST_PER_M)
    actual_out_cost = cost(out_tok, OUT_COST_PER_M)
    actual_cost     = actual_in_cost + actual_out_cost
    saved_cost      = cost(t_saved, IN_COST_PER_M)   # savings are mostly input tokens
    would_cost      = actual_cost + saved_cost

    # Threshold color for context percentage
    pct_fill = round(fill * 100)
    if fill > 0.85:
        pct_color = RED
    elif fill > 0.70:
        pct_color = YELLOW
    else:
        pct_color = ''

    budget_str = f'\u2264{budget} tok/resp' if budget else 'none'
    pct_str    = f'{pct_color}{pct_fill}%{RESET}' if pct_color else f'{pct_fill}%'
    model_str  = f'Sonnet 4.6  {DIM}(in ${IN_COST_PER_M}/1M · out ${OUT_COST_PER_M}/1M){RESET}'

    print()
    print(f'  {PURPLE}{BOLD}◆ PITH{RESET}{DIM} · SESSION STATUS{RESET}')
    print(f'  {DIM}{"─" * 36}{RESET}')
    print()
    print(f'  {DIM}{"Context":<16}{RESET}[{bar(fill)}]  {pct_str}')
    print(f'  {DIM}{"Mode":<16}{RESET}{mode.upper()}')
    print(f'  {DIM}{"Budget":<16}{RESET}{budget_str}')
    print(f'  {DIM}{"Model":<16}{RESET}{model_str}')
    print()
    print(f'  {DIM}Tokens saved (this session){RESET}')
    if toon_s:
        toon_pct = round(toon_s / t_saved * 100) if t_saved else 0
        print(row('TOON (JSON)',   f'{fmt(toon_s)}  {DIM}{toon_pct}%{RESET}', ''))
    if skel_s:
        print(row('Skeletons',     fmt(skel_s)))
    if bash_s:
        print(row('Bash/build',    fmt(bash_s)))
    if out_s:
        print(row('Style output',  fmt(out_s)))
    if compact:
        print(row('Auto-compacts', f'{compact}×'))
    print(row('Total saved',   f'~{fmt(t_saved)} ({pct}%)', GREEN + BOLD))
    print(row('Without Pith', fmt(without)))
    print()
    print(f'  {DIM}Cost (this session){RESET}')
    print(row('Tokens in',    f'{fmt(inp)} → {fmt_cost(actual_in_cost)}'))
    print(row('Tokens out',   f'{fmt(out_tok)} → {fmt_cost(actual_out_cost)}'))
    print(row('Actual spend', fmt_cost(actual_cost), YELLOW))
    print(row('Cost saved',   fmt_cost(saved_cost),  GREEN + BOLD))
    print(row('Without Pith', fmt_cost(would_cost),  RED))
    print()
    print(f'  {DIM}Lifetime{RESET}')
    print(row('Tokens saved', f'~{fmt(total + t_saved)}',              DIM))
    if toon_total + toon_s:
        print(row('TOON saved',  f'~{fmt(toon_total + toon_s)}',       DIM))
    print(row('Cost saved',   fmt_cost(total_cost_saved + saved_cost), DIM))
    print()


if __name__ == '__main__':
    main()
