#!/usr/bin/env python3
"""
Pith Session Report — generates a polished standalone HTML dashboard.

Writes to ~/.pith/report.html and opens it in the browser.
Usage:  python3 report.py          # generate and open
        python3 report.py --no-open  # generate only
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import webbrowser
from pathlib import Path

STATE     = Path.home() / '.pith' / 'state.json'
TELEMETRY = Path.home() / '.pith' / 'telemetry.jsonl'
OUTPUT    = Path.home() / '.pith' / 'report.html'

IN_COST_PER_M  = 3.0
OUT_COST_PER_M = 15.0


def load_state() -> dict:
    try:
        if STATE.exists():
            d = json.loads(STATE.read_text())
            cwd_key = 'proj_' + re.sub(r'[^a-zA-Z0-9]', '',
                      base64.b64encode(os.getcwd().encode()).decode())[:20]
            return d.get(cwd_key, {})
    except Exception:
        pass
    return {}


def load_telemetry(session_start: str) -> list[dict]:
    events: list[dict] = []
    if not TELEMETRY.exists():
        return events
    for line in TELEMETRY.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if not session_start or e.get('session', '') == session_start:
                events.append(e)
        except Exception:
            pass
    return events


def generate_html(s: dict, events: list[dict]) -> str:
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
    limit     = s.get('context_limit', 200_000)
    mode      = s.get('mode', 'off')

    without   = inp + t_saved
    pct       = round(t_saved / without * 100) if without else 0
    fill_pct  = round(inp / limit * 100) if limit else 0

    actual_in_cost  = inp     / 1_000_000 * IN_COST_PER_M
    actual_out_cost = out_tok / 1_000_000 * OUT_COST_PER_M
    actual_cost     = actual_in_cost + actual_out_cost

    tool_saved     = t_saved - out_s
    saved_in_cost  = tool_saved / 1_000_000 * IN_COST_PER_M
    saved_out_cost = out_s      / 1_000_000 * OUT_COST_PER_M
    saved_cost     = saved_in_cost + saved_out_cost
    would_cost     = actual_cost + saved_cost

    comp_ratio = round(without / inp, 1) if inp > 0 else 1.0
    roi        = round(saved_cost / actual_cost, 1) if actual_cost > 0.00001 else 0.0

    tel_labels = json.dumps([e.get('ts', '')[-8:-3] for e in events[-30:]])
    tel_saved  = json.dumps([e.get('before_tokens', 0) - e.get('after_tokens', 0) for e in events[-30:]])
    tel_after  = json.dumps([e.get('after_tokens', 0) for e in events[-30:]])

    buckets_labels = json.dumps(['Skeletons', 'Bash/build', 'Grep', 'TOON', 'Web', 'Offloaded', 'Output mode'])
    buckets_data   = json.dumps([skel_s, bash_s, grep_s, toon_s, web_s, offload_s, out_s])

    def fc(n: float) -> str:
        return f'${n:.4f}' if n >= 0.0001 else '<$0.0001'

    def fk(n: int) -> str:
        if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
        if n >= 1_000:     return f'{n/1_000:.1f}k'
        return str(n)

    def pct_bar(n: int, total: int) -> str:
        """Percentage for inline flow bar widths."""
        return f'{round(n / total * 100) if total > 0 else 0}%'

    session_date = s.get('session_start', '')[:19].replace('T', ' ') or 'this session'

    fill_color  = '#ff453a' if fill_pct > 85 else '#ffd60a' if fill_pct > 70 else '#30d158'
    roi_color   = '#30d158' if roi >= 1 else '#ffd60a'
    ratio_color = '#0a84ff'

    # Build cost table rows
    cost_rows = f"""
      <div class="cost-row">
        <span class="cost-label">Tokens in</span>
        <span class="cost-tokens">{fk(inp)}</span>
        <span class="cost-val">{fc(actual_in_cost)}</span>
      </div>
      <div class="cost-row">
        <span class="cost-label">Tokens out</span>
        <span class="cost-tokens">{fk(out_tok)}</span>
        <span class="cost-val">{fc(actual_out_cost)}</span>
      </div>
      <div class="cost-row cost-row-sep">
        <span class="cost-label" style="color:#fff">Actual spend</span>
        <span class="cost-tokens">—</span>
        <span class="cost-val" style="color:#ffd60a;font-weight:600">{fc(actual_cost)}</span>
      </div>
      <div class="cost-row">
        <span class="cost-label" style="color:#30d158">Tool compression</span>
        <span class="cost-tokens" style="color:#30d158">-{fk(tool_saved)}</span>
        <span class="cost-val" style="color:#30d158">-{fc(saved_in_cost)}</span>
      </div>
      <div class="cost-row">
        <span class="cost-label" style="color:#30d158">Output mode</span>
        <span class="cost-tokens" style="color:#30d158">-{fk(out_s)}</span>
        <span class="cost-val" style="color:#30d158">-{fc(saved_out_cost)}</span>
      </div>
      <div class="cost-row cost-row-sep">
        <span class="cost-label" style="color:#30d158;font-weight:600">Total saved</span>
        <span class="cost-tokens" style="color:#30d158;font-weight:600">-{fk(t_saved)}</span>
        <span class="cost-val" style="color:#30d158;font-weight:600">-{fc(saved_cost)}</span>
      </div>
      <div class="cost-row">
        <span class="cost-label" style="color:#ff453a">Without Pith</span>
        <span class="cost-tokens" style="color:#ff453a">{fk(without)}</span>
        <span class="cost-val" style="color:#ff453a">{fc(would_cost)}</span>
      </div>
    """

    timeline_html = ''
    if events:
        timeline_html = '''
      <div class="card wide r">
        <div class="card-eye">Compression Events</div>
        <div class="card-title">Timeline — tokens kept vs saved per event</div>
        <div class="chart-wrap"><canvas id="timeline"></canvas></div>
      </div>'''

    timeline_js = ''
    if events:
        timeline_js = f"""
new Chart(document.getElementById('timeline'), {{
  type: 'bar',
  data: {{
    labels: {tel_labels},
    datasets: [
      {{ label: 'After (kept)', data: {tel_after}, backgroundColor: '#0a84ff', borderRadius: 3, borderWidth: 0 }},
      {{ label: 'Saved',        data: {tel_saved}, backgroundColor: '#30d158', borderRadius: 3, borderWidth: 0 }}
    ]
  }},
  options: {{
    plugins: {{
      legend: {{ position: 'top', labels: {{ boxWidth: 10, padding: 16, color: 'rgba(255,255,255,0.45)', font: {{ size: 11 }} }} }}
    }},
    scales: {{
      x: {{ stacked: true, grid: {{ color: 'rgba(255,255,255,0.04)' }}, ticks: {{ color: 'rgba(255,255,255,0.3)', font: {{ size: 10 }} }} }},
      y: {{ stacked: true, beginAtZero: true,
            grid: {{ color: 'rgba(255,255,255,0.04)' }},
            ticks: {{ color: 'rgba(255,255,255,0.3)', font: {{ size: 10 }},
                      callback: v => v >= 1000 ? (v/1000).toFixed(1)+'k' : v }} }}
    }},
    animation: {{ duration: 900, easing: 'easeOutQuart' }}
  }}
}});"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Pith Session Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --font:    -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", sans-serif;
  --mono:    "SF Mono", ui-monospace, "Cascadia Code", monospace;
  --bg:      #000;
  --bg1:     #0a0a0a;
  --bg2:     #111;
  --fg:      #fff;
  --fg2:     rgba(255,255,255,0.55);
  --fg3:     rgba(255,255,255,0.25);
  --fg4:     rgba(255,255,255,0.10);
  --border:  rgba(255,255,255,0.08);
  --border2: rgba(255,255,255,0.14);
  --blue:    #0a84ff;
  --green:   #30d158;
  --red:     #ff453a;
  --amber:   #ffd60a;
  --violet:  #bf5af2;
  --emerald: #34d399;
  --indigo:  #818cf8;
  --orange:  #fb923c;
  --ease:    cubic-bezier(0.25,0.46,0.45,0.94);
}}

html {{ scroll-behavior: smooth; }}
body {{
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font);
  -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
  min-height: 100vh;
}}

/* ── NAV ──────────────────────────────────────────────────────────── */
nav {{
  position: fixed; top: 0; left: 0; right: 0; z-index: 999;
  display: flex; align-items: center; height: 48px; padding: 0 28px;
  background: rgba(0,0,0,0.72);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--border);
}}
.nav-logo {{
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; letter-spacing: -0.01em; color: var(--fg);
  text-decoration: none; margin-right: auto;
}}
.nav-meta {{ font-size: 12px; color: var(--fg4); font-family: var(--mono); }}
.nav-badge {{
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
  padding: 3px 10px; border-radius: 980px; text-transform: uppercase;
  background: rgba(10,132,255,0.1); color: var(--blue); border: 1px solid rgba(10,132,255,0.2);
  margin-left: 12px;
}}

/* ── HERO / HEADER ────────────────────────────────────────────────── */
.hero {{
  position: relative; padding: 128px 48px 80px;
  display: flex; flex-direction: column; align-items: flex-start;
  max-width: 1120px; margin: 0 auto; overflow: visible;
}}
.hero-mesh {{ position: absolute; inset: 0; pointer-events: none; z-index: 0; overflow: hidden; }}
.mesh-orb {{ position: absolute; border-radius: 50%; filter: blur(100px); }}
.m1 {{ width:700px;height:400px;background:radial-gradient(ellipse,rgba(10,132,255,0.10) 0%,transparent 70%);top:-10%;right:-5%; }}
.m2 {{ width:400px;height:300px;background:radial-gradient(ellipse,rgba(52,211,153,0.07) 0%,transparent 70%);bottom:0%;left:-5%; }}
.m3 {{ width:300px;height:300px;background:radial-gradient(ellipse,rgba(191,90,242,0.06) 0%,transparent 70%);top:30%;left:40%; }}

.hero-inner {{ position: relative; z-index: 1; }}
.hero-eyebrow {{
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 12px; font-weight: 500; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--fg3); margin-bottom: 20px;
}}
.eyebrow-dot {{
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--blue); box-shadow: 0 0 10px rgba(10,132,255,0.8);
  animation: blink 2.5s ease-in-out infinite;
}}
@keyframes blink {{ 0%,100%{{opacity:1}} 50%{{opacity:0.3}} }}

.hero h1 {{
  font-size: clamp(48px, 6vw, 80px); font-weight: 700;
  letter-spacing: -0.045em; line-height: 0.95; margin-bottom: 16px;
}}
.h1-a {{ color: #fff; }}
.h1-b {{ color: rgba(255,255,255,0.28); display: block; }}
.hero-sub {{
  font-size: 16px; color: var(--fg3); font-family: var(--mono);
  letter-spacing: -0.01em; margin-top: 12px;
}}

/* ── CONTENT WRAPPER ──────────────────────────────────────────────── */
.content {{ max-width: 1120px; margin: 0 auto; padding: 0 48px 96px; }}

/* ── STATS BAR ────────────────────────────────────────────────────── */
.stats {{
  display: grid; grid-template-columns: repeat(4, 1fr);
  border: 1px solid var(--border); border-radius: 20px;
  overflow: hidden; margin-bottom: 2px;
}}
.stat {{
  padding: 40px 36px;
  border-right: 1px solid var(--border);
  transition: background 0.2s;
}}
.stat:last-child {{ border-right: none; }}
.stat:hover {{ background: var(--bg1); }}
.stat-eye {{
  font-size: 10px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--fg4); margin-bottom: 12px;
}}
.stat-n {{
  font-size: clamp(36px, 4vw, 56px); font-weight: 700;
  letter-spacing: -0.05em; line-height: 1; margin-bottom: 8px;
}}
.stat-l {{ font-size: 13px; color: var(--fg3); letter-spacing: -0.01em; line-height: 1.5; }}

/* ── SECTION LABEL ────────────────────────────────────────────────── */
.section-label {{
  font-size: 11px; font-weight: 600; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--blue); margin: 56px 0 20px;
}}
.section-label.green  {{ color: var(--emerald); }}
.section-label.violet {{ color: var(--violet); }}
.section-label.amber  {{ color: var(--amber); }}

/* ── CARDS GRID ───────────────────────────────────────────────────── */
.grid {{
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 2px;
}}
.grid.cols3 {{ grid-template-columns: repeat(3, 1fr); }}
.wide {{ grid-column: 1 / -1; }}

.card {{
  background: var(--bg1); border: 1px solid var(--border);
  border-radius: 20px; padding: 32px;
  transition: background 0.25s var(--ease), border-color 0.25s var(--ease);
}}
.card:hover {{ background: var(--bg2); border-color: var(--border2); }}
.card-eye {{
  font-size: 10px; font-weight: 600; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--fg4); margin-bottom: 8px;
}}
.card-title {{
  font-size: 17px; font-weight: 600; letter-spacing: -0.02em;
  color: #fff; margin-bottom: 24px; line-height: 1.3;
}}
.chart-wrap {{ height: 240px; position: relative; }}

/* ── TOKEN FLOW ───────────────────────────────────────────────────── */
.flow-rows {{ display: flex; flex-direction: column; gap: 16px; }}
.flow-row {{ display: flex; flex-direction: column; gap: 6px; }}
.flow-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
.flow-name {{
  font-size: 13px; color: var(--fg2); letter-spacing: -0.01em;
}}
.flow-val {{
  font-family: var(--mono); font-size: 13px; font-weight: 600;
  letter-spacing: -0.02em;
}}
.flow-track {{
  height: 6px; background: rgba(255,255,255,0.06); border-radius: 980px; overflow: hidden;
}}
.flow-fill {{
  height: 100%; border-radius: 980px;
  transition: width 0.9s cubic-bezier(0.16,1,0.3,1);
}}
.flow-sub {{ font-size: 11px; color: var(--fg4); }}

/* ── COST TABLE ───────────────────────────────────────────────────── */
.cost-rows {{ display: flex; flex-direction: column; }}
.cost-row {{
  display: grid; grid-template-columns: 1fr auto auto;
  align-items: center; gap: 16px;
  padding: 13px 0;
  border-bottom: 1px solid var(--border);
}}
.cost-row:last-child {{ border-bottom: none; }}
.cost-row-sep {{ border-top: 1px solid var(--border2); margin-top: 2px; padding-top: 15px; }}
.cost-label {{ font-size: 14px; color: var(--fg2); letter-spacing: -0.01em; }}
.cost-tokens {{ font-family: var(--mono); font-size: 13px; color: var(--fg3); text-align: right; }}
.cost-val {{ font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--fg2); text-align: right; min-width: 80px; }}

/* ── BUCKET TABLE ─────────────────────────────────────────────────── */
.bucket-rows {{ display: flex; flex-direction: column; gap: 0; }}
.bucket-row {{
  display: flex; align-items: center; gap: 12px;
  padding: 14px 0; border-bottom: 1px solid var(--border);
}}
.bucket-row:last-child {{ border-bottom: none; }}
.bucket-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.bucket-name {{ font-size: 14px; color: var(--fg2); flex: 1; letter-spacing: -0.01em; }}
.bucket-track {{ flex: 2; height: 4px; background: rgba(255,255,255,0.06); border-radius: 980px; overflow: hidden; }}
.bucket-fill {{ height: 100%; border-radius: 980px; }}
.bucket-val {{ font-family: var(--mono); font-size: 12px; color: var(--fg3); min-width: 48px; text-align: right; }}
.bucket-pct {{ font-family: var(--mono); font-size: 11px; color: var(--fg4); min-width: 32px; text-align: right; }}

/* ── REVEAL ───────────────────────────────────────────────────────── */
.r {{ opacity: 0; transform: translateY(24px); transition: opacity 0.7s var(--ease), transform 0.7s var(--ease); }}
.r.on {{ opacity: 1; transform: none; }}
.r.d1 {{ transition-delay: 0.08s; }}
.r.d2 {{ transition-delay: 0.16s; }}
.r.d3 {{ transition-delay: 0.24s; }}
.r.d4 {{ transition-delay: 0.32s; }}

/* ── FOOTER ───────────────────────────────────────────────────────── */
.page-footer {{
  border-top: 1px solid var(--border);
  padding: 20px 48px;
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px; color: var(--fg4); letter-spacing: -0.01em;
  max-width: 1120px; margin: 0 auto;
}}

@media (max-width: 800px) {{
  .hero {{ padding: 100px 24px 60px; }}
  .content {{ padding: 0 24px 64px; }}
  .stats {{ grid-template-columns: repeat(2, 1fr); }}
  .stat:nth-child(2) {{ border-right: none; }}
  .stat:nth-child(3), .stat:nth-child(4) {{ border-top: 1px solid var(--border); }}
  .grid, .grid.cols3 {{ grid-template-columns: 1fr; }}
  .page-footer {{ padding: 20px 24px; flex-direction: column; gap: 8px; text-align: center; }}
}}
</style>
</head>
<body>

<!-- NAV -->
<nav>
  <a class="nav-logo" href="#">
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="10" cy="10" r="9" stroke="rgba(255,255,255,0.18)" stroke-width="1"/>
      <circle cx="10" cy="10" r="5.5" stroke="rgba(255,255,255,0.45)" stroke-width="1.2"/>
      <circle cx="10" cy="10" r="2" fill="#fff"/>
      <line x1="10" y1="1.5" x2="10" y2="3.5" stroke="rgba(255,255,255,0.35)" stroke-width="1" stroke-linecap="round"/>
      <line x1="10" y1="16.5" x2="10" y2="18.5" stroke="rgba(255,255,255,0.35)" stroke-width="1" stroke-linecap="round"/>
      <line x1="1.5" y1="10" x2="3.5" y2="10" stroke="rgba(255,255,255,0.35)" stroke-width="1" stroke-linecap="round"/>
      <line x1="16.5" y1="10" x2="18.5" y2="10" stroke="rgba(255,255,255,0.35)" stroke-width="1" stroke-linecap="round"/>
    </svg>
    Pith
  </a>
  <span class="nav-meta">{session_date}</span>
  <span class="nav-badge">Session Report</span>
</nav>

<!-- HERO -->
<section class="hero r on">
  <div class="hero-mesh">
    <div class="mesh-orb m1"></div>
    <div class="mesh-orb m2"></div>
    <div class="mesh-orb m3"></div>
  </div>
  <div class="hero-inner">
    <div class="hero-eyebrow">
      <div class="eyebrow-dot"></div>
      Token optimization · Pith for Claude Code
    </div>
    <h1>
      <span class="h1-a">{fk(t_saved)} tokens saved.</span>
      <span class="h1-b">{pct}% compression this session.</span>
    </h1>
    <p class="hero-sub">mode: {mode.upper()} &nbsp;·&nbsp; model: Sonnet 4.6 &nbsp;·&nbsp; {session_date}</p>
  </div>
</section>

<div class="content">

<!-- STATS BAR -->
<div class="stats r d1">
  <div class="stat">
    <div class="stat-eye">Compression ratio</div>
    <div class="stat-n" style="color:{ratio_color}">{comp_ratio}:1</div>
    <div class="stat-l">{pct}% fewer tokens than baseline</div>
  </div>
  <div class="stat">
    <div class="stat-eye">Tokens saved</div>
    <div class="stat-n" style="color:var(--emerald)">{fk(t_saved)}</div>
    <div class="stat-l">of {fk(without)} token baseline</div>
  </div>
  <div class="stat">
    <div class="stat-eye">Cost ROI</div>
    <div class="stat-n" style="color:{roi_color}">{roi}×</div>
    <div class="stat-l">{fc(saved_cost)} saved · {fc(actual_cost)} spent</div>
  </div>
  <div class="stat">
    <div class="stat-eye">Context fill</div>
    <div class="stat-n" style="color:{fill_color}">{fill_pct}%</div>
    <div class="stat-l">{fk(inp)} of {fk(limit)} tokens used</div>
  </div>
</div>

<!-- TOKEN FLOW + COST BREAKDOWN -->
<div class="section-label r d2">Token Flow</div>
<div class="grid r d2">

  <div class="card">
    <div class="card-eye">Where tokens went</div>
    <div class="card-title">Baseline vs compressed</div>
    <div class="flow-rows">
      <div class="flow-row">
        <div class="flow-header">
          <span class="flow-name">Baseline (without Pith)</span>
          <span class="flow-val" style="color:rgba(255,255,255,0.35)">{fk(without)}</span>
        </div>
        <div class="flow-track"><div class="flow-fill" style="width:100%;background:rgba(255,255,255,0.12)"></div></div>
      </div>
      <div class="flow-row">
        <div class="flow-header">
          <span class="flow-name" style="color:var(--violet)">Pith intercepted &amp; removed</span>
          <span class="flow-val" style="color:var(--violet)">-{fk(t_saved)}</span>
        </div>
        <div class="flow-track"><div class="flow-fill" style="width:{pct_bar(t_saved, without)};background:var(--violet)"></div></div>
        <div class="flow-sub">{pct}% of baseline removed before reaching context</div>
      </div>
      <div class="flow-row">
        <div class="flow-header">
          <span class="flow-name" style="color:var(--blue)">Sent to context (input)</span>
          <span class="flow-val" style="color:var(--blue)">{fk(inp)}</span>
        </div>
        <div class="flow-track"><div class="flow-fill" style="width:{pct_bar(inp, without)};background:var(--blue)"></div></div>
        <div class="flow-sub">{round(inp/without*100) if without else 0}% of uncompressed baseline</div>
      </div>
      <div class="flow-row">
        <div class="flow-header">
          <span class="flow-name" style="color:var(--amber)">Model output</span>
          <span class="flow-val" style="color:var(--amber)">{fk(out_tok)}</span>
        </div>
        <div class="flow-track"><div class="flow-fill" style="width:{pct_bar(out_tok, max(without,1))};background:var(--amber)"></div></div>
        <div class="flow-sub">tokens generated this session</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-eye">Cost breakdown</div>
    <div class="card-title">Actual vs what you'd have paid</div>
    <div class="cost-rows">
{cost_rows}
    </div>
  </div>

</div>

<!-- CHARTS -->
<div class="section-label green r d2">Savings Analysis</div>
<div class="grid r d3">

  <div class="card">
    <div class="card-eye">By category</div>
    <div class="card-title">Savings distribution</div>
    <div class="chart-wrap"><canvas id="donut"></canvas></div>
  </div>

  <div class="card">
    <div class="card-eye">Category detail</div>
    <div class="card-title">Tokens saved per source</div>
    <div class="bucket-rows" id="bucket-rows"></div>
  </div>

</div>

<div class="grid r d4" style="margin-top:2px">
  <div class="card">
    <div class="card-eye">Input vs output vs saved</div>
    <div class="card-title">Token volume comparison</div>
    <div class="chart-wrap"><canvas id="bar"></canvas></div>
  </div>
  {timeline_html}
</div>

</div><!-- /content -->

<footer class="page-footer r">
  <span>◆ Pith Session Report &nbsp;·&nbsp; {session_date}</span>
  <span>Run <code style="font-family:var(--mono);opacity:0.6">/pith report</code> to refresh &nbsp;·&nbsp; <code style="font-family:var(--mono);opacity:0.6">/pith status</code> for terminal view</span>
</footer>

<script>
// ── Chart.js global defaults ──────────────────────────────────────────────────
Chart.defaults.color = 'rgba(255,255,255,0.3)';
Chart.defaults.borderColor = 'rgba(255,255,255,0.04)';
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif";
Chart.defaults.font.size = 12;

const BLUE    = '#0a84ff';
const GREEN   = '#30d158';
const AMBER   = '#ffd60a';
const VIOLET  = '#bf5af2';
const EMERALD = '#34d399';
const ORANGE  = '#fb923c';
const INDIGO  = '#818cf8';
const PINK    = '#f472b6';
const RED     = '#ff453a';

// ── Bucket data ───────────────────────────────────────────────────────────────
const bucketLabels = {buckets_labels};
const bucketData   = {buckets_data};
const bucketColors = [INDIGO, ORANGE, EMERALD, VIOLET, PINK, GREEN, AMBER];
const totalSaved   = bucketData.reduce((a, b) => a + b, 0);

// ── Build bucket detail rows ──────────────────────────────────────────────────
const bucketContainer = document.getElementById('bucket-rows');
bucketLabels.forEach((label, i) => {{
  if (bucketData[i] <= 0) return;
  const pct   = totalSaved > 0 ? Math.round(bucketData[i] / totalSaved * 100) : 0;
  const width = pct + '%';
  const val   = bucketData[i] >= 1000 ? (bucketData[i]/1000).toFixed(1)+'k' : bucketData[i];
  const row = document.createElement('div');
  row.className = 'bucket-row';
  row.innerHTML = `
    <div class="bucket-dot" style="background:${{bucketColors[i]}}"></div>
    <div class="bucket-name">${{label}}</div>
    <div class="bucket-track"><div class="bucket-fill" style="width:${{width}};background:${{bucketColors[i]}}"></div></div>
    <div class="bucket-val">${{val}}</div>
    <div class="bucket-pct">${{pct}}%</div>
  `;
  bucketContainer.appendChild(row);
}});

// ── Donut ─────────────────────────────────────────────────────────────────────
const nonZeroLabels = [], nonZeroData = [], nonZeroColors = [];
bucketLabels.forEach((l, i) => {{
  if (bucketData[i] > 0) {{
    nonZeroLabels.push(l);
    nonZeroData.push(bucketData[i]);
    nonZeroColors.push(bucketColors[i]);
  }}
}});

new Chart(document.getElementById('donut'), {{
  type: 'doughnut',
  data: {{
    labels: nonZeroLabels.length ? nonZeroLabels : ['No data yet'],
    datasets: [{{
      data: nonZeroData.length ? nonZeroData : [1],
      backgroundColor: nonZeroColors.length ? nonZeroColors : ['rgba(255,255,255,0.06)'],
      borderWidth: 0,
      hoverBorderWidth: 2,
      hoverBorderColor: 'rgba(255,255,255,0.2)',
    }}]
  }},
  options: {{
    cutout: '68%',
    plugins: {{
      legend: {{
        position: 'right',
        labels: {{ boxWidth: 10, padding: 14, color: 'rgba(255,255,255,0.45)', font: {{ size: 11 }} }}
      }}
    }},
    animation: {{ duration: 900, easing: 'easeOutQuart' }}
  }}
}});

// ── Bar ───────────────────────────────────────────────────────────────────────
new Chart(document.getElementById('bar'), {{
  type: 'bar',
  data: {{
    labels: ['Input', 'Output', 'Saved'],
    datasets: [{{
      data: [{inp}, {out_tok}, {t_saved}],
      backgroundColor: [BLUE, AMBER, GREEN],
      borderRadius: 6,
      borderWidth: 0,
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{
        beginAtZero: true,
        grid: {{ color: 'rgba(255,255,255,0.04)' }},
        ticks: {{ color: 'rgba(255,255,255,0.3)', font: {{ size: 11 }},
                  callback: v => v >= 1000 ? (v/1000).toFixed(1)+'k' : v }}
      }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: 'rgba(255,255,255,0.3)', font: {{ size: 12 }} }} }}
    }},
    animation: {{ duration: 900, easing: 'easeOutQuart' }}
  }}
}});

{timeline_js}

// ── Reveal on scroll ──────────────────────────────────────────────────────────
const observer = new IntersectionObserver(entries => {{
  entries.forEach(e => {{ if (e.isIntersecting) e.target.classList.add('on'); }});
}}, {{ threshold: 0.05 }});
document.querySelectorAll('.r').forEach(el => observer.observe(el));
</script>
</body>
</html>"""


def main():
    p = argparse.ArgumentParser(description='Pith session report generator')
    p.add_argument('--no-open', action='store_true', help='Generate but do not open in browser')
    args = p.parse_args()

    s         = load_state()
    session   = s.get('session_start', '')
    events    = load_telemetry(session)
    html      = generate_html(s, events)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding='utf-8')
    print(f'[PITH REPORT] written → {OUTPUT}')

    if not args.no_open:
        url = OUTPUT.resolve().as_uri()
        webbrowser.open(url)
        print(f'[PITH REPORT] opening {url}')


if __name__ == '__main__':
    main()
