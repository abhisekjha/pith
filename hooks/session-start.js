#!/usr/bin/env node
'use strict';
// Pith — SessionStart hook
// Runs once per session. Responsibilities:
//   1. Detect first run → inject onboarding prompt
//   2. Cache-optimize CLAUDE.md (suggest stable prefix)
//   3. Inject active output-mode rules
//   4. Inject wiki-mode rules if wiki mode is on
//   5. Inject token budget if set
//   6. Announce active automatic features

const fs = require('fs');
const path = require('path');
const { loadConfig, loadProjectState, saveProjectState, pluginRoot } = require('./config');

const config = loadConfig();
const proj = loadProjectState();

// Reset per-session counters
saveProjectState({
  session_start:           new Date().toISOString(),
  tool_savings_session:    0,
  toon_savings_session:    0,
  skeleton_savings_session:0,
  bash_savings_session:    0,
  output_savings_session:  0,
  compact_count_session:   0,
  input_tokens_est:        0,
  output_tokens_est:       0,
  compact_nudged:          false,
});

const root = pluginRoot();
const output = [];

// ── 1. FIRST RUN ────────────────────────────────────────────────────────────
if (!proj.setup_done) {
  let setupScript = '';
  try {
    setupScript = fs.readFileSync(
      path.join(root, 'skills', 'pith-setup', 'SKILL.md'), 'utf8'
    ).replace(/^---[\s\S]*?---\s*/, '');
  } catch (e) {
    setupScript = `On the user's very first message this session, introduce Pith in 2 sentences, then ask:
"New project or existing codebase?" Offer to build a project wiki.
Be conversational — one question at a time. Do not dump information upfront.`;
  }
  process.stdout.write('PITH FIRST RUN\n\n' + setupScript);
  process.exit(0);
}

// ── 2. ACTIVE FEATURES ANNOUNCEMENT ─────────────────────────────────────────
const features = [];
if (config.tool_compress) features.push('tool compression');
if (config.auto_compact)  features.push(`auto-compact at ${Math.round(config.auto_compact_threshold * 100)}%`);
if (features.length) output.push(`PITH ACTIVE: ${features.join(', ')}.`);

// ── 3. OUTPUT MODE RULES ─────────────────────────────────────────────────────
const mode = proj.mode || config.default_mode;
if (mode && mode !== 'off') {
  let skillContent = '';
  try {
    skillContent = fs.readFileSync(
      path.join(root, 'skills', 'pith', 'SKILL.md'), 'utf8'
    ).replace(/^---[\s\S]*?---\s*/, '');
    // Filter level table to only the active level row
    skillContent = skillContent.split('\n').filter(line => {
      const m = line.match(/^\|\s*\*\*(\w+)\*\*\s*\|/);
      return !m || m[1] === mode;
    }).join('\n');
  } catch (e) {
    const fallback = {
      precise: 'Drop filler/hedging/pleasantries. Full sentences. Professional.',
      lean:    'Drop articles (a/an/the). Fragments OK. Short synonyms. Drop filler.',
      ultra:   'Max compression. Abbreviate common terms. Arrows (→). Tables > prose.',
    };
    skillContent = `${fallback[mode] || fallback.lean} Technical terms exact. Code unchanged. ACTIVE EVERY RESPONSE until /pith off.`;
  }
  output.push(`OUTPUT MODE: ${mode.toUpperCase()}\n\n${skillContent}`);
}

// ── 4. WIKI MODE RULES ───────────────────────────────────────────────────────
if (proj.wiki_mode) {
  try {
    const wikiContent = fs.readFileSync(
      path.join(root, 'skills', 'pith-wiki', 'SKILL.md'), 'utf8'
    ).replace(/^---[\s\S]*?---\s*/, '');
    output.push('WIKI MODE ACTIVE\n\n' + wikiContent);
  } catch (e) {
    output.push('WIKI MODE ACTIVE. Maintain wiki pages as we work. After decisions/solutions, offer to save to wiki.');
  }
}

// ── 5. TOKEN BUDGET ──────────────────────────────────────────────────────────
if (proj.budget) {
  output.push(`TOKEN BUDGET: ≤${proj.budget} tokens per response. Count as you write. Stop when done. No apology for brevity.`);
}

// ── 6. CLAUDE.MD CACHE NUDGE ─────────────────────────────────────────────────
try {
  const claudeMd = path.join(process.env.CLAUDE_CWD || process.cwd(), 'CLAUDE.md');
  if (fs.existsSync(claudeMd) && !proj.cache_optimized) {
    const lines = fs.readFileSync(claudeMd, 'utf8').split('\n').length;
    if (lines > 60) {
      output.push(
        `PITH TIP: CLAUDE.md is ${lines} lines and re-read every turn at full cost. ` +
        `Run \`/pith optimize-cache\` to restructure it for prompt caching and cut that cost by ~80%.`
      );
    }
  }
} catch (e) { /* silent */ }

process.stdout.write(output.filter(Boolean).join('\n\n---\n\n'));
