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


# Pricing per 1M tokens: (input, output)
PRICING: dict[str, tuple[float, float]] = {
    'opus-4-7':   (5.0,  25.0),  'opus-4-6':   (5.0,  25.0),  'opus-4-5':  (5.0,  25.0),
    'opus-4-1':   (15.0, 75.0),  'opus-4':     (15.0, 75.0),
    'sonnet-4-6': (3.0,  15.0),  'sonnet-4-5': (3.0,  15.0),  'sonnet-4':  (3.0,  15.0),
    'sonnet-3-7': (3.0,  15.0),
    'haiku-4-5':  (1.0,  5.0),   'haiku-3-5':  (0.8,  4.0),
    'opus-3':     (15.0, 75.0),  'haiku-3':    (0.25, 1.25),
}

def get_pricing(model: str | None) -> tuple[float, float]:
    if not model:
        return (3.0, 15.0)
    m = model.lower().replace('claude-', '').replace('_', '-')
    for key in sorted(PRICING, key=len, reverse=True):
        if key in m:
            return PRICING[key]
    return (3.0, 15.0)

def model_label(model: str | None) -> str:
    if not model:
        return 'Unknown (defaulting to Sonnet 4.6 rates)'
    return model


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



def flow_chart(inp: int, out_tok: int, t_saved: int, without: int, limit: int) -> str:
    """ASCII token flow diagram — baseline vs actual vs output."""
    W = 32   # bar width in chars
    lines = []

    def flow_bar(n: int, total: int, color: str) -> str:
        filled = min(round(n / total * W), W) if total > 0 else 0
        return f'{color}{"█" * filled}{"░" * (W - filled)}{RESET}'

    if without > 0:
        lines.append(f'  {DIM}Token Flow{RESET}')
        lines.append(f'  {DIM}{"─" * 54}{RESET}')
        lines.append(
            f'  {DIM}{"Baseline (no Pith)":<20}{RESET}'
            f'{flow_bar(without, without, DIM)}  {fmt(without)}'
        )
        lines.append(
            f'  {DIM}{"Pith compressed":<20}{RESET}'
            f'{flow_bar(inp, without, PURPLE)}  {fmt(inp)}'
            f'  {DIM}({round(inp/without*100) if without else 0}% of baseline){RESET}'
        )
        if t_saved > 0:
            lines.append(
                f'  {GREEN}{"  └ saved":<20}{RESET}'
                f'{flow_bar(t_saved, without, GREEN)}  -{fmt(t_saved)}'
                f'  {GREEN}({round(t_saved/without*100) if without else 0}% removed){RESET}'
            )
        lines.append('')

    if inp > 0 or out_tok > 0:
        total_io = inp + out_tok
        lines.append(f'  {DIM}{"Input  →":<20}{RESET}{flow_bar(inp, max(total_io,1), PURPLE)}  {fmt(inp)} tok')
        lines.append(f'  {DIM}{"Output ←":<20}{RESET}{flow_bar(out_tok, max(total_io,1), YELLOW)}  {fmt(out_tok)} tok')
        lines.append(f'  {DIM}{"Context fill":<20}{RESET}{flow_bar(inp, limit, RED if inp/limit>0.85 else YELLOW if inp/limit>0.70 else DIM)}  {round(inp/limit*100) if limit else 0}% of {fmt(limit)}')

    return '\n'.join(lines)


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
    toon_total   = s.get('toon_savings_total', 0)
    total_cost_saved = s.get('cost_saved_total', 0.0)
    turn_count   = s.get('turn_count_session', 0)

    model_id = s.get('model')
    IN_COST_PER_M, OUT_COST_PER_M = get_pricing(model_id)

    fill    = inp / limit if limit else 0
    # without = tool-compression baseline (input side only; output baseline added below)
    without = inp + t_saved
    total_saved = t_saved + out_s
    full_baseline = without + out_s
    pct     = round(total_saved / full_baseline * 100) if full_baseline else 0

    # Cost calculations
    actual_in_cost  = cost(inp,     IN_COST_PER_M)
    actual_out_cost = cost(out_tok, OUT_COST_PER_M)
    actual_cost     = actual_in_cost + actual_out_cost

    # t_saved (tool compression) and out_s (output mode) are independent buckets —
    # out_s is NOT a subset of t_saved. Never subtract one from the other.
    saved_in_cost  = cost(t_saved, IN_COST_PER_M)    # tool savings save input tokens
    saved_out_cost = cost(out_s,   OUT_COST_PER_M)   # output mode saves output tokens
    saved_cost_val = saved_in_cost + saved_out_cost
    would_cost     = actual_cost + saved_cost_val
    total_saved    = t_saved + out_s

    # Threshold color for context bar
    pct_fill = round(fill * 100)
    if fill > 0.85:   pct_color = RED
    elif fill > 0.70: pct_color = YELLOW
    else:             pct_color = ''

    budget_str = f'\u2264{budget} tok/resp' if budget else 'none'
    pct_str    = f'{pct_color}{pct_fill}%{RESET}' if pct_color else f'{pct_fill}%'
    _mlabel    = model_id if model_id else 'unknown (Sonnet 4.6 rates)'
    model_str  = f'{_mlabel}  {DIM}(in ${IN_COST_PER_M}/1M · out ${OUT_COST_PER_M}/1M){RESET}'

    # ── Key metrics ───────────────────────────────────────────────────────────
    comp_ratio   = round(without / inp, 1) if inp > 0 else None
    # ROI = cost_saved / actual_cost: for every $1 spent, saved $ROI
    roi          = round(saved_cost_val / actual_cost, 1) if actual_cost > 0.00001 else None
    # Per-response averages (turn_count from stop hook, each response = 1 turn)
    avg_out      = round(out_tok / turn_count) if turn_count > 0 else None
    avg_in_delta = round(inp / turn_count)     if turn_count > 0 else None

    print()
    print(f'  {PURPLE}{BOLD}◆ PITH{RESET}{DIM} · SESSION STATUS{RESET}')
    print(f'  {DIM}{"─" * 36}{RESET}')
    print()
    print(f'  {DIM}{"Mode":<16}{RESET}{mode.upper()}')
    print(f'  {DIM}{"Budget":<16}{RESET}{budget_str}')
    print(f'  {DIM}{"Model":<16}{RESET}{model_str}')

    # ── Token flow diagram ────────────────────────────────────────────────────
    print()
    print(flow_chart(inp, out_tok, t_saved, without, limit))

    # Key metrics row (only when we have real session data)
    if total_saved > 0 and inp > 0:
        print()
        ratio_str = f'{GREEN}{BOLD}{comp_ratio}:1{RESET}' if comp_ratio else '—'
        roi_str   = f'{GREEN}{BOLD}{roi}×{RESET}' if roi else '—'
        print(f'  {DIM}{"Compression ratio":<18}{RESET}{ratio_str}  '
              f'{DIM}({pct}% fewer tokens — {fmt(total_saved)} saved of {fmt(full_baseline)} baseline){RESET}')
        if roi:
            print(f'  {DIM}{"Cost ROI":<18}{RESET}{roi_str}  '
                  f'{DIM}(per $1 spent → ${roi} saved  ·  {fmt_cost(saved_cost_val)} total){RESET}')
    if turn_count > 0 and avg_out:
        print(f'  {DIM}{"Per response":<18}{RESET}'
              f'{DIM}avg output {fmt(avg_out)} tok  ·  {turn_count} turns this session{RESET}')

    # ── Savings breakdown ─────────────────────────────────────────────────────
    print()
    print(f'  {DIM}Savings this session  (without Pith: {fmt(full_baseline)} tokens){RESET}')
    print()

    # Output mode savings are estimated relative to actual output tokens, not tool savings.
    # Share % for output mode uses out_tok as denominator (not t_saved) to avoid >100% display.
    tool_buckets = [
        ('Skeletons',   skel_s,    'file reads → imports + signatures'),
        ('Bash/build',  bash_s,    'build, install, test output'),
        ('Grep/search', grep_s,    'search results capped at 25'),
        ('TOON (JSON)', toon_s,    'JSON → compact key=value format'),
        ('Web fetch',   web_s,     'HTML stripped to text'),
        ('Offloaded',   offload_s, 'large results moved to file'),
    ]
    any_bucket = any(v > 0 for _, v, _ in tool_buckets) or out_s > 0
    if any_bucket:
        for label, val, desc in tool_buckets:
            if val <= 0:
                continue
            share_pct = round(val / t_saved * 100) if t_saved else 0
            mini      = pct_bar(val, t_saved)
            print(f'  {DIM}{label:<16}{RESET}{mini} {fmt(val):>6}  {DIM}{share_pct}%  {desc}{RESET}')
        if out_s > 0:
            # Show output mode savings relative to actual output (makes sense contextually)
            out_pct = round(out_s / out_tok * 100) if out_tok > 0 else 0
            mini    = pct_bar(out_s, max(out_s, out_tok))
            print(f'  {DIM}{"Output mode":<16}{RESET}{mini} {fmt(out_s):>6}  '
                  f'{DIM}est {out_pct}% of output  lean/ultra response compression{RESET}')
        print()

    # Summary line
    if total_saved > 0:
        print(f'  {GREEN}{BOLD}{"Total saved":<16}{RESET}  '
              f'{GREEN}{BOLD}{fmt(total_saved)} tokens  ({pct}% of what this session would have used){RESET}')
        print(f'  {DIM}{"Actual used":<16}{RESET}  {fmt(inp)} tokens  '
              f'{DIM}({100 - pct}% of uncompressed baseline){RESET}')
        # One-line plain-English insight
        if t_saved > 0 and out_s > 0:
            dominant = 'output mode' if out_s >= t_saved else 'tool compression'
            dom_pct  = round(max(out_s, t_saved) / total_saved * 100) if total_saved else 0
            mode_label = mode.upper()
            insight = f'{dominant} driving {dom_pct}% of savings'
            if dominant == 'output mode':
                insight += f' — {mode_label} active'
            else:
                insight += ' — hooks doing the heavy lifting'
        elif out_s > 0:
            insight = f'output mode ({proj.get("mode","off").upper()}) is your only active savings — try /pith focus or reading large files to trigger tool compression'
        elif t_saved > 0:
            insight = f'tool compression active — enable /pith lean or ultra to also compress responses'
        else:
            insight = ''
        if insight:
            print(f'  {DIM}{"  →":<16}{RESET}{DIM}{insight}{RESET}')
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
        print(row('  Tool compress',  f'-{fmt(t_saved)} tok  {fmt_cost(saved_in_cost)}', DIM))
    if saved_out_cost > 0:
        print(row('  Output mode',    f'-{fmt(out_s)} tok  {fmt_cost(saved_out_cost)}', DIM))
    print(row('Cost saved',    fmt_cost(saved_cost_val), GREEN + BOLD))
    print(row('Without Pith',  fmt_cost(would_cost),  RED))

    # ── Lifetime ──────────────────────────────────────────────────────────────
    lifetime_total = total + total_saved
    lifetime_cost  = total_cost_saved + saved_cost_val
    # Fallback: cost_saved_total was 0 before stop.js fix (sessions before the patch).
    # If accumulated cost is lower than what token count implies (all input at $3/1M),
    # use the token-based floor so historical sessions aren't silently zeroed out.
    lifetime_cost_floor = (lifetime_total / 1_000_000) * IN_COST_PER_M
    lifetime_cost = max(lifetime_cost, lifetime_cost_floor)
    print()
    print(f'  {DIM}Lifetime{RESET}')
    print(row('Tokens saved', f'~{fmt(lifetime_total)}',  DIM))
    if toon_total + toon_s:
        print(row('TOON saved',  f'~{fmt(toon_total + toon_s)}', DIM))
    if offload_t + offload_s:
        print(row('Offloaded',   f'~{fmt(offload_t + offload_s)}', DIM))
    print(row('Cost saved',   fmt_cost(lifetime_cost), DIM))
    print()
    print(f'  {DIM}Run /pith report to open an interactive HTML dashboard{RESET}')
    print()


if __name__ == '__main__':
    main()
