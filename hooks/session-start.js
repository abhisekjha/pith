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
  session_start:            new Date().toISOString(),
  tokens_saved_session:     0,   // ← was missing — caused phantom 100% savings
  tool_savings_session:     0,
  toon_savings_session:     0,
  skeleton_savings_session: 0,
  bash_savings_session:     0,
  offload_savings_session:  0,
  output_savings_session:   0,
  grep_savings_session:     0,
  web_savings_session:      0,
  compact_count_session:    0,
  escalation_count_session: 0,
  hindsight_nudged:         false,
  input_tokens_est:         0,
  output_tokens_est:        0,
  turn_count:               0,
  stale_results:            [],
  compact_nudged:           false,
});

// ── Clean up tmp files older than 24 h ────────────────────────────────────
try {
  const os      = require('os');
  const tmpDir  = require('path').join(os.homedir(), '.pith', 'tmp');
  const fs      = require('fs');
  const cutoff  = Date.now() - 24 * 60 * 60 * 1000;
  if (fs.existsSync(tmpDir)) {
    fs.readdirSync(tmpDir).forEach(f => {
      const fp = require('path').join(tmpDir, f);
      try {
        if (fs.statSync(fp).mtimeMs < cutoff) fs.unlinkSync(fp);
      } catch (e) { /* skip locked files */ }
    });
  }
} catch (e) { /* never block session start */ }

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

// ── 2a. USTYNOV PRINCIPLE (injected once per project) ────────────────────────
// Ustynov [2026] arXiv:2604.07502 — abbreviations save 17% input tokens but
// cause re-reads that increase total session cost by 67%.
if (!proj.ustynov_injected) {
  output.push(
    'NAMING CONVENTION — Ustynov Principle (2026):\n' +
    'Use descriptive names in all generated code.\n' +
    '  ✓  calculateUserSessionTotal   →  intent clear, one-shot\n' +
    '  ✗  calcSessTotal               →  ambiguous, causes re-reads (+67% cost)\n' +
    'This applies to functions, variables, and file names you create or rename.'
  );
  const { saveProjectState: _sps } = require('./config');
  _sps({ ustynov_injected: true });
}

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

// ── Phase 9: Cache-Lock ──────────────────────────────────────────────────────
// If the session-start content is identical to last session, emit a compact
// 1-line summary instead of the full rules. Saves ~300 tokens per session start
// on repeat sessions with unchanged settings.
// Dynamic values (token counts, timestamps) must NEVER appear in `output` —
// they belong in prompt-submit.js (user turn), not here (system context).
const fullContent = output.filter(Boolean).join('\n\n---\n\n');
if (fullContent) {
  const crypto      = require('crypto');
  const contentHash = crypto.createHash('md5').update(fullContent).digest('hex').slice(0, 8);
  const lastHash    = proj.session_injection_hash;  // from OLD state (before session resets)

  if (lastHash && lastHash === contentHash) {
    // Repeat session — same settings → compact 1-line summary
    const parts = [];
    if (mode && mode !== 'off') parts.push(`mode=${mode.toUpperCase()}`);
    if (proj.wiki_mode) parts.push('wiki=ON');
    if (proj.budget)    parts.push(`budget=\u2264${proj.budget}`);
    const summary = parts.length ? parts.join(' \u00b7 ') : 'no active settings';
    process.stdout.write(
      `[PITH: ${summary} \u2014 settings unchanged, full rules omitted to protect cache. /pith help for reference.]`
    );
  } else {
    // New or changed settings — emit full content, save hash for next session
    saveProjectState({ session_injection_hash: contentHash });
    process.stdout.write(fullContent);
  }
}
