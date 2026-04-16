#!/usr/bin/env node
'use strict';
// Pith — UserPromptSubmit hook
// Parses /pith commands, tracks token estimates, injects per-message context.

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { loadConfig, loadProjectState, saveProjectState, pluginRoot } = require('./config');

const OUTPUT_MODES = new Set(['lean', 'precise', 'ultra', 'off']);
// Subcommands handled entirely by Claude Code skill files — hook just skips them
const SKILL_CMDS   = new Set(['debug', 'review', 'arch', 'plan', 'commit', 'install', 'uninstall']);

let raw = '';
process.stdin.on('data', c => { raw += c; });
process.stdin.on('end', () => {
  try {
    const data   = JSON.parse(raw);
    const prompt = (data.prompt || '').trim();
    const lower  = prompt.toLowerCase();
    const config = loadConfig();
    const proj   = loadProjectState();
    const root   = pluginRoot();
    const out    = [];

    // ── /pith <arg> ────────────────────────────────────────────────────────
    if (lower.startsWith('/pith')) {
      const parts = prompt.trim().split(/\s+/);
      const arg   = (parts[1] || '').toLowerCase();
      const rest  = parts.slice(2).join(' ');

      if (OUTPUT_MODES.has(arg)) {
        saveProjectState({ mode: arg });
        out.push(arg === 'off'
          ? 'PITH OUTPUT COMPRESSION: deactivated.'
          : modeRules(arg, root));

      } else if (arg === '' || arg === 'on') {
        const m = proj.mode && proj.mode !== 'off' ? proj.mode : (config.default_mode !== 'off' ? config.default_mode : 'lean');
        saveProjectState({ mode: m });
        out.push(modeRules(m, root));

      } else if (arg === 'wiki') {
        if (rest) {
          // /pith wiki "question" — query the wiki
          out.push(wikiQuery(rest, root));
        } else {
          // /pith wiki — toggle wiki mode.
          // Debounce: UserPromptSubmit hook fires before the slash command
          // re-calls this script.  If the toggle happened within the last 2s,
          // skip re-toggling and just echo current state.
          const now = Date.now();
          const lastToggle = proj._wiki_toggle_ms || 0;
          if (now - lastToggle < 2000) {
            // Already toggled — confirm state without mutating
            out.push(proj.wiki_mode
              ? 'PITH WIKI MODE: active. I will maintain the project wiki as we work.\n\n' + wikiModeRules(root)
              : 'PITH WIKI MODE: deactivated.');
          } else {
            const next = !proj.wiki_mode;
            saveProjectState({ wiki_mode: next, _wiki_toggle_ms: now });
            out.push(next
              ? 'PITH WIKI MODE: active. I will maintain the project wiki as we work.\n\n' + wikiModeRules(root)
              : 'PITH WIKI MODE: deactivated.');
          }
        }

      } else if (arg === 'ingest') {
        // /pith ingest <file>
        const filePath = rest.trim();
        if (!filePath) {
          out.push('[PITH: /pith ingest requires a file path. Example: /pith ingest raw/sources/article.md]');
        } else {
          out.push(runTool('ingest.py', [filePath], root));
        }

      } else if (arg === 'lint') {
        // wiki_guard.py is the lint tool (not wiki_lint.py)
        out.push(runTool('wiki_guard.py', [], root));

      } else if (arg === 'status') {
        out.push(runTool('health.py', [], root));

      } else if (arg === 'recall') {
        // /pith recall — restore last session's mode, wiki, and budget
        const saved = { mode: proj.mode, wiki_mode: proj.wiki_mode, budget: proj.budget };
        const parts2 = [];
        if (saved.mode && saved.mode !== 'off') parts2.push(`mode=${saved.mode.toUpperCase()}`);
        if (saved.wiki_mode) parts2.push('wiki=ON');
        if (saved.budget)    parts2.push(`budget=${saved.budget}`);
        if (parts2.length === 0) {
          out.push('PITH RECALL: no saved state found for this project.');
        } else {
          const injections = [];
          if (saved.mode && saved.mode !== 'off') injections.push(modeRules(saved.mode, root));
          if (saved.wiki_mode) injections.push('PITH WIKI MODE: active. I will maintain the project wiki as we work.\n\n' + wikiModeRules(root));
          if (saved.budget) injections.push(`HARD TOKEN LIMIT: ≤${saved.budget} tokens this response. Count as you write. Stop when done.`);
          out.push(`PITH RECALL: restored ${parts2.join(', ')}.`);
          injections.forEach(i => out.push(i));
        }

      } else if (arg === 'configure') {
        // /pith configure — interactive config wizard
        const cur = {
          mode:         proj.mode || config.default_mode || 'off',
          wiki:         proj.wiki_mode ? 'ON' : 'OFF',
          budget:       proj.budget    ? String(proj.budget) : 'none',
          autoCompact:  config.auto_compact ? 'ON' : 'OFF',
        };
        out.push(
          `PITH CONFIGURE WIZARD\n\n` +
          `Current settings:\n` +
          `  mode         = ${cur.mode}\n` +
          `  wiki mode    = ${cur.wiki}\n` +
          `  token budget = ${cur.budget}\n` +
          `  auto-compact = ${cur.autoCompact}\n\n` +
          `Walk the user through each setting one at a time:\n` +
          `1. Output mode? [lean / precise / ultra / off]\n` +
          `2. Enable wiki mode? [yes / no]\n` +
          `3. Token budget? [number or skip]\n` +
          `4. Auto-compact at 70%? [yes / no]\n\n` +
          `Apply each answer immediately via saveProjectState. End with a one-line summary.`
        );

      } else if (arg === 'tour') {
        // /pith tour [step-number] — interactive guided tour
        const stepArg = rest ? parseInt(rest, 10) : null;
        const tourAction = (!isNaN(stepArg) && stepArg >= 1 && stepArg <= 7)
          ? `--step ${stepArg} --action set`
          : '--action get';
        out.push(runTool('tour.py', [], root, tourAction) + '\n\n' + loadTourSkill(root));

      } else if (arg === 'setup') {
        // Re-run onboarding
        saveProjectState({ setup_done: false });
        out.push('PITH SETUP: resetting onboarding. On next message I will guide you through setup again.');

      } else if (arg === 'optimize-cache') {
        out.push(optimizeCache(root));

      } else if (SKILL_CMDS.has(arg)) {
        // /pith debug, /pith review, /pith install, etc. — handled by Claude Code skill system
        // No injection needed; skill file provides instructions.
      }
    }

    // ── /budget <n|off> ────────────────────────────────────────────────────
    if (lower.startsWith('/budget')) {
      const parts = prompt.trim().split(/\s+/);
      const arg   = (parts[1] || '').toLowerCase();
      if (arg === 'off' || arg === '0') {
        saveProjectState({ budget: null });
        out.push('TOKEN BUDGET: cleared.');
      } else {
        const n = parseInt(arg, 10);
        if (!isNaN(n) && n > 0) {
          saveProjectState({ budget: n });
          out.push(`HARD TOKEN LIMIT: ≤${n} tokens this response. Count as you write. Stop when done. No apology for brevity.`);
        }
      }
    }

    // ── /focus <file> ──────────────────────────────────────────────────────
    if (lower.startsWith('/focus ')) {
      const filePath = prompt.slice('/focus '.length).trim();
      out.push(runTool('focus.py', [filePath, '--question', data.prompt || ''], root));
    }

    // ── Per-message: inject active budget ──────────────────────────────────
    const budget = loadProjectState().budget;
    if (budget && !lower.startsWith('/pith') && !lower.startsWith('/budget')) {
      out.push(`[TOKEN LIMIT: ≤${budget} tokens this response]`);
    }

    // ── Token estimate tracking ────────────────────────────────────────────
    const est = Math.ceil(prompt.length / 4);
    saveProjectState({ input_tokens_est: (proj.input_tokens_est || 0) + est });

    if (out.length) process.stdout.write(out.filter(Boolean).join('\n\n'));
  } catch (e) { /* silent */ }
  process.exit(0);
});

// ── Helpers ──────────────────────────────────────────────────────────────────

function modeRules(mode, root) {
  try {
    let content = fs.readFileSync(path.join(root, 'skills', 'pith', 'SKILL.md'), 'utf8')
      .replace(/^---[\s\S]*?---\s*/, '');
    content = content.split('\n').filter(l => {
      const m = l.match(/^\|\s*\*\*(\w+)\*\*\s*\|/);
      return !m || m[1] === mode;
    }).join('\n');
    return `PITH MODE: ${mode.toUpperCase()}\n\n${content}`;
  } catch (e) {
    const rules = { precise: 'Drop filler/hedging. Full sentences.', lean: 'Drop articles. Fragments OK.', ultra: 'Max compression. Abbreviate. Tables.' };
    return `PITH MODE: ${mode.toUpperCase()}. ${rules[mode] || rules.lean} Technical terms exact. Code unchanged. ACTIVE EVERY RESPONSE.`;
  }
}

function wikiModeRules(root) {
  try {
    return fs.readFileSync(path.join(root, 'skills', 'pith-wiki', 'SKILL.md'), 'utf8')
      .replace(/^---[\s\S]*?---\s*/, '');
  } catch (e) {
    return 'Maintain wiki pages as we work. After decisions, bugs solved, or architecture discussions, offer to save to wiki/. Keep pages concise and cross-linked.';
  }
}

function wikiQuery(question, root) {
  return runTool('wiki.py', ['--question', question], root);
}

function optimizeCache(root) {
  try {
    const claudeMd = path.join(process.env.CLAUDE_CWD || process.cwd(), 'CLAUDE.md');
    if (!fs.existsSync(claudeMd)) return '[PITH: No CLAUDE.md found in current directory]';
    const content = fs.readFileSync(claudeMd, 'utf8');
    return `PITH CACHE OPTIMIZATION TASK:\n\nRead CLAUDE.md (${content.split('\n').length} lines).\n` +
      'Restructure it so stable sections (project overview, file structure, conventions) come FIRST, ' +
      'and volatile sections (current tasks, recent changes) come LAST. ' +
      'The first ~80% should never change session-to-session. ' +
      'Write the restructured version back to CLAUDE.md. ' +
      'Then confirm how many tokens are now in the stable prefix.';
  } catch (e) {
    return `[PITH: cache optimization failed — ${e.message}]`;
  }
}

function runTool(script, args, root, extraArgs) {
  try {
    const toolPath = path.join(root, 'tools', script);
    if (!fs.existsSync(toolPath)) return `[PITH: tool ${script} not found]`;
    const escaped = args.map(a => `"${String(a).replace(/"/g, '\\"')}"`).join(' ');
    const cmd = `python3 "${toolPath}" ${escaped}${extraArgs ? ' ' + extraArgs : ''}`;
    return execSync(cmd, { timeout: 30000, encoding: 'utf8', cwd: process.env.CLAUDE_CWD || process.cwd() }).trim();
  } catch (e) {
    return `[PITH: ${script} failed — ${(e.stderr || e.message || '').slice(0, 200)}]`;
  }
}

function loadTourSkill(root) {
  try {
    return fs.readFileSync(path.join(root, 'skills', 'pith-tour', 'SKILL.md'), 'utf8')
      .replace(/^---[\s\S]*?---\s*/, '');
  } catch (e) {
    return 'PITH TOUR: run the interactive 7-step tour. Guide the user through each Pith feature hands-on.';
  }
}
