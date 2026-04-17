#!/usr/bin/env python3
"""
Pith Wiki Graph Generator
Scans ./wiki/ for .md files, extracts [[wikilinks]], and renders an
interactive force-directed graph as a standalone wiki-graph.html.
"""

import os
import re
import json
import webbrowser
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
WIKI_DIR = Path.cwd() / "wiki"
OUTPUT_FILE = Path.cwd() / "wiki-graph.html"
WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')


# ── 1. Parse wiki ─────────────────────────────────────────────────────────────
def parse_wiki(wiki_dir: Path) -> tuple[list[dict], list[dict]]:
    if not wiki_dir.exists():
        print(f"[error] wiki directory not found: {wiki_dir}", file=sys.stderr)
        sys.exit(1)

    node_set: dict[str, dict] = {}
    edges: list[dict] = []

    md_files = sorted(wiki_dir.rglob("*.md"))
    if not md_files:
        print("[warn] No .md files found in wiki/")

    for md_path in md_files:
        rel = md_path.relative_to(wiki_dir)
        if md_path.name == 'CLAUDE.md':
            continue
        node_id = str(rel.with_suffix(""))
        label = md_path.stem
        group = rel.parts[0] if len(rel.parts) > 1 else "root"

        if node_id not in node_set:
            node_set[node_id] = {"id": node_id, "label": label,
                                 "path": str(rel), "group": group}

        content = md_path.read_text(encoding="utf-8", errors="ignore")
        for match in WIKILINK_RE.finditer(content):
            raw = match.group(1).strip()
            target_label = raw.lower().replace(" ", "-").replace(".md", "")
            target_id = _resolve_target(target_label, node_set)
            if target_id:
                edges.append({"source": node_id, "target": target_id})
            else:
                ghost_id = f"ghost/{target_label}"
                if ghost_id not in node_set:
                    node_set[ghost_id] = {
                        "id": ghost_id,
                        "label": raw,
                        "path": None,
                        "group": "ghost",
                    }
                edges.append({"source": node_id, "target": ghost_id})

    seen_edges: set[tuple] = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    nodes = list(node_set.values())
    print(f"[pith graph] {len(nodes)} nodes, {len(unique_edges)} edges")
    return nodes, unique_edges


def _resolve_target(target_label: str, node_set: dict) -> str | None:
    for node_id, node in node_set.items():
        if node["label"].lower().replace(" ", "-") == target_label:
            return node_id
    for node_id in node_set:
        if node_id.lower().endswith(target_label):
            return node_id
    return None


# ── 2. Generate HTML ──────────────────────────────────────────────────────────
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Pith Wiki Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:           #0c0c14;
  --bg-glass:     rgba(12,12,20,0.75);
  --border:       rgba(255,255,255,0.10);
  --border-btn:   rgba(255,255,255,0.14);
  --border-hover: rgba(255,255,255,0.28);
  --text:         #f0f0f8;
  --text-muted:   #9b9bb8;
  --text-dim:     #5a5a78;
  --pill-bg:      rgba(12,12,20,0.88);
  --font:         -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", sans-serif;
  --mono:         "SF Mono", ui-monospace, "Cascadia Code", monospace;
}
.light {
  --bg:           #f5f5fa;
  --bg-glass:     rgba(245,245,250,0.82);
  --border:       rgba(0,0,0,0.09);
  --border-btn:   rgba(0,0,0,0.15);
  --border-hover: rgba(0,0,0,0.30);
  --text:         #0c0c14;
  --text-muted:   #4a4a6a;
  --text-dim:     #9898b8;
  --pill-bg:      rgba(245,245,250,0.92);
}

html, body {
  width: 100%; height: 100vh;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  -webkit-font-smoothing: antialiased;
  transition: background 0.3s;
}

/* ── Mesh background ─────────────────────────────────────────────────────── */
#mesh {
  position: fixed; inset: 0; pointer-events: none; z-index: 0;
  overflow: hidden;
}
.orb {
  position: absolute; border-radius: 50%; filter: blur(120px);
  animation: drift 18s ease-in-out infinite alternate;
}
.orb1 { width:700px;height:500px; background:radial-gradient(ellipse,rgba(99,102,241,0.13) 0%,transparent 70%); top:-15%;right:-5%; animation-delay:0s; }
.orb2 { width:500px;height:400px; background:radial-gradient(ellipse,rgba(52,211,153,0.09) 0%,transparent 70%); bottom:-10%;left:-5%; animation-delay:-6s; }
.orb3 { width:350px;height:350px; background:radial-gradient(ellipse,rgba(167,139,250,0.08) 0%,transparent 70%); top:40%;left:30%; animation-delay:-12s; }
.orb4 { width:400px;height:280px; background:radial-gradient(ellipse,rgba(244,114,182,0.06) 0%,transparent 70%); bottom:20%;right:20%; animation-delay:-4s; }

@keyframes drift { 0%{transform:translate(0,0) scale(1)} 100%{transform:translate(30px,20px) scale(1.08)} }

/* ── Glassmorphism header ─────────────────────────────────────────────────── */
#header {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 20;
  display: flex; align-items: center; gap: 12px;
  padding: 9px 18px;
  background: var(--bg-glass);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 4px 32px rgba(0,0,0,0.28), 0 1px 0 rgba(255,255,255,0.04) inset;
  white-space: nowrap;
}
.logo {
  font-size: 13px; font-weight: 700; color: #a78bfa;
  letter-spacing: 0.08em; text-transform: uppercase;
}
.divider { width:1px; height:16px; background:var(--border); }
.stats { font-size: 12px; color: var(--text-dim); font-family: var(--mono); }

.btn {
  background: transparent;
  border: 1px solid var(--border-btn);
  color: var(--text-muted);
  padding: 4px 12px; border-radius: 7px;
  font-size: 12px; font-family: var(--font); cursor: pointer;
  transition: all 0.15s;
}
.btn:hover { background: rgba(167,139,250,0.10); border-color: rgba(167,139,250,0.35); color: #a78bfa; }
.btn:active { background: rgba(167,139,250,0.18); }
#theme-btn { font-size: 14px; padding: 4px 10px; }

/* ── Legend ──────────────────────────────────────────────────────────────── */
#legend {
  position: fixed; bottom: 20px; left: 20px; z-index: 20;
  display: flex; flex-direction: column; gap: 7px;
  padding: 12px 16px;
  background: var(--bg-glass);
  backdrop-filter: blur(16px) saturate(160%);
  -webkit-backdrop-filter: blur(16px) saturate(160%);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.22);
}
#legend-title { font-size: 9px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-dim); margin-bottom: 2px; }
.legend-item { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--text-muted); }
.legend-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; box-shadow: 0 0 6px currentColor; }

/* ── Hints ───────────────────────────────────────────────────────────────── */
#hints {
  position: fixed; bottom: 20px; right: 20px; z-index: 20;
  display: flex; flex-direction: column; gap: 4px;
  padding: 10px 14px;
  background: var(--bg-glass);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.22);
}
.hint { font-size: 10px; color: var(--text-dim); font-family: var(--mono); }

/* ── SVG ─────────────────────────────────────────────────────────────────── */
svg { position: relative; z-index: 1; width:100vw; height:100vh; display:block; }

/* ── Links ───────────────────────────────────────────────────────────────── */
.link { stroke-width: 1.5px; transition: stroke-opacity 0.2s, stroke-width 0.2s; }
.link.faded  { stroke-opacity: 0.04 !important; }
.link.active { stroke-width: 2.5px; stroke-opacity: 1 !important; }

/* ── Nodes ───────────────────────────────────────────────────────────────── */
.node { cursor: grab; }
.node:active { cursor: grabbing; }
.node circle { stroke-width: 1.8px; transition: opacity 0.2s, r 0.2s; }
.node circle.ghost { stroke-dasharray: 4 3; fill: transparent; opacity: 0.25; }
.node.faded circle  { opacity: 0.06; }
.node.faded .pill-bg { opacity: 0.06; }
.node.faded text    { opacity: 0.06; }
.node.focused circle { stroke-width: 2.5px; }
.pill-bg { transition: opacity 0.2s; }
.node text {
  font-size: 11px; pointer-events: none; dominant-baseline: central;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
  transition: opacity 0.2s, fill 0.2s;
}
.node.focused text { font-weight: 700; font-size: 12px; }

/* ── Tooltip ─────────────────────────────────────────────────────────────── */
#tooltip {
  position: fixed;
  background: var(--bg-glass);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 11px 15px;
  font-size: 12px; color: var(--text);
  pointer-events: none; opacity: 0; transition: opacity 0.15s;
  max-width: 300px; line-height: 1.7; z-index: 30;
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  box-shadow: 0 8px 32px rgba(0,0,0,0.32);
}
#tooltip.visible { opacity: 1; }
.tip-label { font-weight: 700; font-size: 13px; margin-bottom: 3px; }
.tip-group { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.55; }
.tip-path  { color: var(--text-dim); font-size: 10px; font-family: var(--mono); margin-top: 4px; }
.tip-links { color: var(--text-dim); font-size: 11px; margin-top: 5px; }
</style>
</head>
<body>

<div id="mesh">
  <div class="orb orb1"></div>
  <div class="orb orb2"></div>
  <div class="orb orb3"></div>
  <div class="orb orb4"></div>
</div>

<div id="header">
  <span class="logo">◆ Pith Wiki</span>
  <div class="divider"></div>
  <span class="stats" id="stats">loading…</span>
  <button class="btn" onclick="resetZoom()">Reset view</button>
  <button class="btn" id="theme-btn" onclick="toggleTheme()" title="Toggle light/dark">☀︎</button>
</div>

<div id="legend">
  <div id="legend-title">Node types</div>
  <div class="legend-item"><div class="legend-dot" style="background:#34d399;color:#34d399"></div>Decisions</div>
  <div class="legend-item"><div class="legend-dot" style="background:#818cf8;color:#818cf8"></div>Concepts</div>
  <div class="legend-item"><div class="legend-dot" style="background:#fb923c;color:#fb923c"></div>Entities</div>
  <div class="legend-item"><div class="legend-dot" style="background:#f472b6;color:#f472b6"></div>Syntheses</div>
  <div class="legend-item"><div class="legend-dot" style="background:#a78bfa;color:#a78bfa"></div>Root</div>
  <div class="legend-item"><div class="legend-dot" style="background:#4b5563;color:#4b5563;border:1.5px dashed #6b7280"></div>Ghost</div>
</div>

<div id="hints">
  <div class="hint">scroll → zoom</div>
  <div class="hint">drag bg → pan</div>
  <div class="hint">drag node → pin</div>
  <div class="hint">hover → focus</div>
</div>

<div id="tooltip"></div>
<svg id="graph"></svg>

<script>
const RAW_NODES = __NODES__;
const RAW_EDGES = __EDGES__;

// ── Palette ───────────────────────────────────────────────────────────────────
const GROUP_COLORS = {
  decisions:  "#34d399",
  concepts:   "#818cf8",
  entities:   "#fb923c",
  syntheses:  "#f472b6",
  root:       "#a78bfa",
  ghost:      "#4b5563",
};
const DEFAULT_COLOR = "#818cf8";

function nodeColor(d) { return GROUP_COLORS[d.group] || DEFAULT_COLOR; }

function nodeRadius(d, degree) {
  if (d.group === "ghost") return 5;
  const base = 11;
  return Math.min(base + Math.sqrt(degree || 0) * 2.5, 26);
}

// ── Degree index ──────────────────────────────────────────────────────────────
const nodeById = {};
RAW_NODES.forEach(n => { nodeById[n.id] = n; });

const degree = {};
RAW_NODES.forEach(n => { degree[n.id] = 0; });

const edges = RAW_EDGES.filter(e => nodeById[e.source] && nodeById[e.target]);
edges.forEach(e => {
  const s = e.source.id || e.source;
  const t = e.target.id || e.target;
  if (degree[s] !== undefined) degree[s]++;
  if (degree[t] !== undefined) degree[t]++;
});

const neighbours = {};
RAW_NODES.forEach(n => { neighbours[n.id] = new Set(); });

document.getElementById("stats").textContent =
  `${RAW_NODES.length} pages · ${edges.length} links`;

// ── SVG / zoom ────────────────────────────────────────────────────────────────
const svg = d3.select("#graph");
const W = window.innerWidth, H = window.innerHeight;
const g = svg.append("g");

const zoom = d3.zoom()
  .scaleExtent([0.05, 12])
  .on("zoom", e => g.attr("transform", e.transform));

svg.call(zoom);

function resetZoom() {
  svg.transition().duration(600).ease(d3.easeCubicOut)
     .call(zoom.transform, d3.zoomIdentity.translate(W/2, H/2).scale(0.85));
}

// ── Simulation ────────────────────────────────────────────────────────────────
const simulation = d3.forceSimulation(RAW_NODES)
  .force("link",    d3.forceLink(edges).id(d => d.id).distance(d => {
    const s = nodeById[d.source.id || d.source];
    const t = nodeById[d.target.id || d.target];
    return s && t && s.group === t.group ? 90 : 130;
  }).strength(0.3))
  .force("charge",  d3.forceManyBody().strength(d => -(260 + (degree[d.id] || 0) * 30)))
  .force("center",  d3.forceCenter(0, 0))
  .force("collide", d3.forceCollide(d => nodeRadius(d, degree[d.id]) + 20));

// ── Arrowhead marker ──────────────────────────────────────────────────────────
svg.append("defs").append("marker")
  .attr("id", "arrow")
  .attr("viewBox", "0 -4 8 8")
  .attr("refX", 18).attr("refY", 0)
  .attr("markerWidth", 5).attr("markerHeight", 5)
  .attr("orient", "auto")
  .append("path")
  .attr("d", "M0,-4L8,0L0,4")
  .attr("fill", "rgba(255,255,255,0.15)");

// ── Links ─────────────────────────────────────────────────────────────────────
const link = g.append("g").attr("class", "links")
  .selectAll("line")
  .data(edges)
  .join("line")
  .attr("class", "link")
  .attr("stroke", d => {
    const s = nodeById[d.source.id || d.source];
    return s ? nodeColor(s) : "#555";
  })
  .attr("stroke-opacity", 0.30);

// ── Node groups ───────────────────────────────────────────────────────────────
const nodeG = g.append("g").attr("class", "nodes")
  .selectAll("g")
  .data(RAW_NODES)
  .join("g")
  .attr("class", "node")
  .call(d3.drag()
    .on("start", dragStart)
    .on("drag",  dragged)
    .on("end",   dragEnd));

// Glow filter
const defs = svg.append("defs");
Object.entries(GROUP_COLORS).forEach(([group, color]) => {
  const hex = color.replace('#','');
  const r = parseInt(hex.slice(0,2),16);
  const gr = parseInt(hex.slice(2,4),16);
  const b = parseInt(hex.slice(4,6),16);
  const f = defs.append("filter").attr("id", `glow-${group}`).attr("x","-50%").attr("y","-50%").attr("width","200%").attr("height","200%");
  f.append("feGaussianBlur").attr("stdDeviation","4").attr("result","blur");
  const merge = f.append("feMerge");
  merge.append("feMergeNode").attr("in","blur");
  merge.append("feMergeNode").attr("in","SourceGraphic");
});

// Outer glow circle (larger, very transparent)
nodeG.filter(d => d.group !== "ghost")
  .append("circle")
  .attr("r", d => nodeRadius(d, degree[d.id]) + 8)
  .attr("fill", d => nodeColor(d))
  .attr("fill-opacity", 0.06)
  .attr("stroke", "none")
  .attr("pointer-events", "none");

// Main circle
nodeG.append("circle")
  .attr("r",    d => nodeRadius(d, degree[d.id]))
  .attr("fill", d => d.group === "ghost" ? "transparent" : nodeColor(d))
  .attr("fill-opacity", d => d.group === "ghost" ? 0 : 0.22)
  .attr("stroke", nodeColor)
  .attr("filter", d => d.group !== "ghost" ? `url(#glow-${d.group})` : "none")
  .classed("ghost", d => d.group === "ghost");

// Label pill background
nodeG.append("rect")
  .attr("class", "pill-bg")
  .attr("rx", 4).attr("ry", 4)
  .attr("fill", "rgba(12,12,20,0.80)")
  .attr("opacity", 0);

// Label text
nodeG.append("text")
  .text(d => d.label)
  .attr("x", d => nodeRadius(d, degree[d.id]) + 7)
  .attr("y", 0)
  .attr("fill", d => nodeColor(d));

// ── Build neighbour index ─────────────────────────────────────────────────────
edges.forEach(e => {
  const s = e.source.id || e.source;
  const t = e.target.id || e.target;
  if (neighbours[s]) neighbours[s].add(t);
  if (neighbours[t]) neighbours[t].add(s);
});

// ── Hover / focus ─────────────────────────────────────────────────────────────
const tooltip = document.getElementById("tooltip");

nodeG
  .on("mouseover", (event, d) => {
    const nb = neighbours[d.id] || new Set();
    nodeG.classed("faded",  n => n.id !== d.id && !nb.has(n.id))
         .classed("focused", n => n.id === d.id);
    link.classed("faded",  e => { const s=e.source.id||e.source,t=e.target.id||e.target; return s!==d.id&&t!==d.id; })
        .classed("active", e => { const s=e.source.id||e.source,t=e.target.id||e.target; return s===d.id||t===d.id; });

    const outDeg = edges.filter(e => (e.source.id||e.source) === d.id).length;
    const inDeg  = edges.filter(e => (e.target.id||e.target) === d.id).length;
    tooltip.innerHTML =
      `<div class="tip-label" style="color:${nodeColor(d)}">${d.label}</div>` +
      `<div class="tip-group" style="color:${nodeColor(d)}">${d.group}</div>` +
      (d.path ? `<div class="tip-path">wiki/${d.path}</div>` : '<div class="tip-path" style="color:#f472b6">⚬ ghost — not yet created</div>') +
      `<div class="tip-links">↗ ${outDeg} outbound &nbsp;·&nbsp; ↙ ${inDeg} inbound</div>`;
    tooltip.classList.add("visible");
  })
  .on("mousemove", event => {
    tooltip.style.left = (event.clientX + 20) + "px";
    tooltip.style.top  = (event.clientY - 14) + "px";
  })
  .on("mouseout", () => {
    nodeG.classed("faded", false).classed("focused", false);
    link.classed("faded", false).classed("active", false);
    tooltip.classList.remove("visible");
  });

// ── Tick ──────────────────────────────────────────────────────────────────────
const PILL_PAD_X = 5, PILL_PAD_Y = 3;

simulation.on("tick", () => {
  link
    .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x).attr("y2", d => d.target.y);

  nodeG.attr("transform", d => `translate(${d.x},${d.y})`);

  nodeG.each(function(d) {
    const grp  = d3.select(this);
    const txt  = grp.select("text").node();
    const rect = grp.select("rect.pill-bg");
    if (!txt) return;
    try {
      const bb = txt.getBBox();
      rect
        .attr("x",      bb.x - PILL_PAD_X)
        .attr("y",      bb.y - PILL_PAD_Y)
        .attr("width",  bb.width  + PILL_PAD_X * 2)
        .attr("height", bb.height + PILL_PAD_Y * 2)
        .attr("opacity", 1);
    } catch(e) {}
  });
});

// ── Drag ──────────────────────────────────────────────────────────────────────
function dragStart(event, d) {
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}
function dragged(event, d) { d.fx = event.x; d.fy = event.y; }
function dragEnd(event, d) {
  if (!event.active) simulation.alphaTarget(0);
  d.fx = null; d.fy = null;
}

// ── Theme toggle ──────────────────────────────────────────────────────────────
let isDark = true;

function applyTheme() {
  document.body.classList.toggle("light", !isDark);
  document.getElementById("theme-btn").textContent = isDark ? "☀︎" : "☾";
  const pillColor = isDark ? "rgba(12,12,20,0.80)" : "rgba(245,245,250,0.92)";
  nodeG.selectAll("rect.pill-bg").attr("fill", pillColor);
}

function toggleTheme() {
  isDark = !isDark;
  applyTheme();
}

// ── Initial view ──────────────────────────────────────────────────────────────
svg.call(zoom.transform, d3.zoomIdentity.translate(W/2, H/2).scale(0.85));
window.addEventListener("resize", () =>
  simulation.force("center", d3.forceCenter(0,0)).alpha(0.1).restart()
);
</script>
</body>
</html>
"""


def generate_html(nodes: list[dict], edges: list[dict]) -> str:
    html = HTML_TEMPLATE
    html = html.replace("__NODES__", json.dumps(nodes, indent=2))
    html = html.replace("__EDGES__", json.dumps(edges, indent=2))
    return html


# ── 3. Main ───────────────────────────────────────────────────────────────────
def main():
    nodes, edges = parse_wiki(WIKI_DIR)
    html = generate_html(nodes, edges)

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"[pith graph] written → {OUTPUT_FILE}")

    url = OUTPUT_FILE.resolve().as_uri()
    webbrowser.open(url)
    print(f"[pith graph] opening {url}")


if __name__ == "__main__":
    main()
