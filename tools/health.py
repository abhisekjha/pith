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


def pct_bar(n: int, total: int, w: int = 12) -> str:
    """Tiny inline bar showing n's share of total."""
    if total <= 0 or n <= 0:
        return ' ' * w
    filled = min(round(n / total * w), w)
    return f'{DIM}{"▪" * filled}{"·" * (w - filled)}{RESET}'


def main():
    s = load()
    inp       = s.get('input_tokens_est', 0)
    out_tok   = s.get('output_tokens_est', 0)
    t_saved   = s.get('tokens_saved_session', 0)
    toon_s    = s.get('toon_savings_session', 0)
    skel_s    = s.get('skeleton_savings_session', 0)
    bash_s    = s.get('bash_savings_session', 0)
    grep_s    = s.get('grep_savings_session', 0)
    web_s     = s.get('web_savings_session', 0)
    offload_s = s.get('offload_savings_session', 0)
    out_s     = s.get('output_savings_session', 0)
    offload_t = s.get('offload_savings_total', 0)
    compact   = s.get('compact_count_session', 0)
    esc_s     = s.get('escalation_count_session', 0)
    limit     = s.get('context_limit', 200_000)
    mode      = s.get('mode', 'off')
    budget    = s.get('budget')
    total     = s.get('tokens_saved_total', 0)
    toon_total= s.get('toon_savings_total', 0)
    total_cost_saved = s.get('cost_saved_total', 0.0)

    fill    = inp / limit if limit else 0
    # Total without Pith = what we consumed + what Pith removed
    without = inp + t_saved
    pct     = round(t_saved / without * 100) if without else 0

    # Cost calculations
    actual_in_cost  = cost(inp,     IN_COST_PER_M)
    actual_out_cost = cost(out_tok, OUT_COST_PER_M)
    actual_cost     = actual_in_cost + actual_out_cost

    # Savings split by token type:
    #   Tool compression saves INPUT tokens (results re-read each turn) → $3/1M
    #   Output mode compression saves OUTPUT tokens → $15/1M (5× more valuable)
    tool_saved_tokens = t_saved - out_s          # everything except output mode
    saved_in_cost     = cost(tool_saved_tokens, IN_COST_PER_M)
    saved_out_cost    = cost(out_s,             OUT_COST_PER_M)
    saved_cost_val    = saved_in_cost + saved_out_cost
    would_cost        = actual_cost + saved_cost_val

    # Threshold color for context bar
    pct_fill = round(fill * 100)
    if fill > 0.85:   pct_color = RED
    elif fill > 0.70: pct_color = YELLOW
    else:             pct_color = ''

    budget_str = f'\u2264{budget} tok/resp' if budget else 'none'
    pct_str    = f'{pct_color}{pct_fill}%{RESET}' if pct_color else f'{pct_fill}%'
    model_str  = f'Sonnet 4.6  {DIM}(in ${IN_COST_PER_M}/1M · out ${OUT_COST_PER_M}/1M){RESET}'

    # ── State-of-the-art metrics ──────────────────────────────────────────────
    # Compression ratio: tokens_without / tokens_used  (e.g. 3.1:1)
    comp_ratio  = round(without / inp, 1) if inp > 0 else None
    # Cost ROI: cost_saved / actual_cost  (e.g. 4.2× — you get $4.20 back per $1 spent)
    roi         = round(saved_cost_val / actual_cost, 1) if actual_cost > 0.00001 else None
    # Context efficiency: how much MORE work fits vs without Pith
    ctx_eff     = round(without / inp, 1) if inp > 0 else None   # same as comp_ratio

    print()
    print(f'  {PURPLE}{BOLD}◆ PITH{RESET}{DIM} · SESSION STATUS{RESET}')
    print(f'  {DIM}{"─" * 36}{RESET}')
    print()
    print(f'  {DIM}{"Context":<16}{RESET}[{bar(fill)}]  {pct_str}')
    print(f'  {DIM}{"Mode":<16}{RESET}{mode.upper()}')
    print(f'  {DIM}{"Budget":<16}{RESET}{budget_str}')
    print(f'  {DIM}{"Model":<16}{RESET}{model_str}')

    # Key metrics row (only when we have real session data)
    if t_saved > 0 and inp > 0:
        print()
        ratio_str = f'{GREEN}{BOLD}{comp_ratio}:1{RESET}' if comp_ratio else '—'
        roi_str   = f'{GREEN}{BOLD}{roi}×{RESET}' if roi else '—'
        pct_red   = pct
        print(f'  {DIM}{"Compression ratio":<18}{RESET}{ratio_str}  '
              f'{DIM}({pct_red}% fewer tokens — {fmt(t_saved)} saved of {fmt(without)} baseline){RESET}')
        if roi:
            print(f'  {DIM}{"Cost ROI":<18}{RESET}{roi_str}  '
                  f'{DIM}({fmt_cost(saved_cost_val)} saved per {fmt_cost(actual_cost)} spent){RESET}')

    # ── Savings breakdown ─────────────────────────────────────────────────────
    print()
    print(f'  {DIM}Savings this session  (without Pith: {fmt(without)} tokens){RESET}')
    print()

    buckets = [
        ('Skeletons',      skel_s,    'file reads → imports + signatures'),
        ('Bash/build',     bash_s,    'build, install, test output'),
        ('Grep/search',    grep_s,    'search results capped at 25'),
        ('TOON (JSON)',    toon_s,    'JSON → compact key=value format'),
        ('Web fetch',      web_s,     'HTML stripped to text'),
        ('Offloaded',      offload_s, 'large results moved to file'),
        ('Output mode',    out_s,     f'lean/ultra response compression  {DIM}(est){RESET}'),
    ]
    any_bucket = any(v > 0 for _, v, _ in buckets)
    if any_bucket:
        for label, val, desc in buckets:
            if val <= 0:
                continue
            share_pct = round(val / t_saved * 100) if t_saved else 0
            mini      = pct_bar(val, t_saved)
            print(f'  {DIM}{label:<16}{RESET}{mini} {fmt(val):>6}  {DIM}{share_pct}%  {desc}{RESET}')
        print()

    # Summary line
    if t_saved > 0:
        print(f'  {GREEN}{BOLD}{"Total saved":<16}{RESET}  '
              f'{GREEN}{BOLD}{fmt(t_saved)} tokens  ({pct}% of what this session would have used){RESET}')
        print(f'  {DIM}{"Actual used":<16}{RESET}  {fmt(inp)} tokens  '
              f'{DIM}({100 - pct}% of uncompressed baseline){RESET}')
    else:
        print(f'  {DIM}No savings recorded yet — tool compression fires on first tool call.{RESET}')

    if compact:  print(f'\n  {DIM}Auto-compacts:  {compact}×{RESET}')
    if esc_s:    print(f'  {DIM}Auto-escalations: {esc_s}×{RESET}')

    # ── Cost ─────────────────────────────────────────────────────────────────
    print()
    print(f'  {DIM}Cost (this session){RESET}')
    print(row('Tokens in',     f'{fmt(inp)} → {fmt_cost(actual_in_cost)}'))
    print(row('Tokens out',    f'{fmt(out_tok)} → {fmt_cost(actual_out_cost)}'))
    print(row('Actual spend',  fmt_cost(actual_cost), YELLOW))
    print()
    if saved_in_cost > 0:
        print(row('  Tool compress',  f'-{fmt(tool_saved_tokens)} tok  {fmt_cost(saved_in_cost)}', DIM))
    if saved_out_cost > 0:
        print(row('  Output mode',    f'-{fmt(out_s)} tok  {fmt_cost(saved_out_cost)}', DIM))
    print(row('Cost saved',    fmt_cost(saved_cost_val), GREEN + BOLD))
    print(row('Without Pith',  fmt_cost(would_cost),  RED))

    # ── Lifetime ──────────────────────────────────────────────────────────────
    lifetime_total = total + t_saved
    lifetime_cost  = total_cost_saved + saved_cost_val
    print()
    print(f'  {DIM}Lifetime{RESET}')
    print(row('Tokens saved', f'~{fmt(lifetime_total)}',  DIM))
    if toon_total + toon_s:
        print(row('TOON saved',  f'~{fmt(toon_total + toon_s)}', DIM))
    if offload_t + offload_s:
        print(row('Offloaded',   f'~{fmt(offload_t + offload_s)}', DIM))
    print(row('Cost saved',   fmt_cost(lifetime_cost), DIM))
    print()


if __name__ == '__main__':
    main()
