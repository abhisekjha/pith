#!/usr/bin/env node
'use strict';
// Pith — UserPromptSubmit hook
// Parses /pith commands, tracks token estimates, injects per-message context.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { loadConfig, loadProjectState, saveProjectState, pluginRoot } = require('./config');

const OUTPUT_MODES = new Set(['lean', 'precise', 'ultra', 'off']);
// Subcommands handled entirely by Claude Code skill files — hook just skips them
const SKILL_CMDS   = new Set(['debug', 'review', 'arch', 'plan', 'commit', 'install', 'uninstall']);

// Read actual token counts from the current session's transcript JSONL.
// transcript_path may be provided by Claude Code; if not, we derive it from session_id + cwd.
function syncTranscriptTokens(data) {
  try {
    const os = require('os');
    let transcriptPath = data.transcript_path;

    if (!transcriptPath && data.session_id) {
      const cwd  = data.cwd || process.env.CLAUDE_CWD || process.cwd();
      const slug = cwd.replace(/\//g, '-');
      const dir  = path.join(os.homedir(), '.claude', 'projects', slug);
      const file = path.join(dir, `${data.session_id}.jsonl`);
      if (fs.existsSync(file)) transcriptPath = file;
    }

    if (!transcriptPath || !fs.existsSync(transcriptPath)) return;

    // Output: sum all turns (each turn's output is independent)
    // Input:  use ONLY the latest assistant entry — each turn's input already
    //         includes full conversation history, so summing causes massive double-count
    let outputTokens = 0, latestInputTokens = 0;
    const lines = fs.readFileSync(transcriptPath, 'utf8').split('\n');
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const d = JSON.parse(line);
        if (d.type === 'assistant' && d.message && d.message.usage) {
          const u = d.message.usage;
          outputTokens += u.output_tokens || 0;
          // Overwrite each time — last entry wins (= current context size)
          latestInputTokens = (u.input_tokens || 0)
                            + (u.cache_read_input_tokens || 0)
                            + (u.cache_creation_input_tokens || 0);
        }
      } catch (_) { /* skip malformed line */ }
    }

    if (outputTokens > 0 || latestInputTokens > 0) {
      saveProjectState({
        output_tokens_est:    outputTokens,
        output_tokens_actual: outputTokens,
        // input = current context window size (latest turn only)
        ...(latestInputTokens > 0 ? { input_tokens_est: latestInputTokens } : {}),
      });
    }
  } catch (_) { /* silent — never block a session */ }
}

// Parse optional --slash <prefix> flag. When set, stdin is treated as the
// raw argument text (not JSON), and the prompt is built internally as
// `${prefix} ${stdin}`. This lets slash-command .md files pipe user arguments
// in via a heredoc instead of embedding them into a shell-quoted JSON string,
// which eliminates shell-injection via quotes/$()/backticks in user input.
const argv = process.argv.slice(2);
let slashPrefix = null;
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--slash' && i + 1 < argv.length) {
    slashPrefix = argv[i + 1];
    if (!/^\/[A-Za-z0-9_-]+$/.test(slashPrefix)) {
      process.stderr.write('[PITH: --slash prefix must match /[A-Za-z0-9_-]+]\n');
      process.exit(2);
    }
    i++;
  }
}

let raw = '';
process.stdin.on('data', c => { raw += c; });
process.stdin.on('end', () => {
  try {
    let data;
    if (slashPrefix) {
      // stdin is raw user-typed argument text; strip one trailing newline
      const args = raw.replace(/\n$/, '');
      data = { prompt: args ? `${slashPrefix} ${args}` : slashPrefix };
    } else {
      data = JSON.parse(raw);
    }
    const prompt = (data.prompt || '').trim();
    const lower  = prompt.toLowerCase();
    const config = loadConfig();

    // Sync real output/input token counts from transcript before reading state
    syncTranscriptTokens(data);

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

      } else if (arg === 'update') {
        out.push(buildUpdateInstructions(root));

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
          ? ['--step', String(stepArg), '--action', 'set']
          : ['--action', 'get'];
        out.push(runTool('tour.py', tourAction, root) + '\n\n' + loadTourSkill(root));

      } else if (arg === 'setup') {
        // Re-run onboarding
        saveProjectState({ setup_done: false });
        out.push('PITH SETUP: resetting onboarding. On next message I will guide you through setup again.');

      } else if (arg === 'optimize-cache') {
        out.push(optimizeCache(root));

      } else if (arg === 'help' || arg === 'commands') {
        out.push(
`PITH COMMAND REFERENCE

── Output Compression ──────────────────────────────────────────
  /pith lean             Fragment-style responses, drop articles
  /pith precise          Full sentences, drop filler/hedging
  /pith ultra            Max compression, abbreviate, use tables
  /pith off              Deactivate compression
  /pith on               Restore last saved mode

── Structured Formats ──────────────────────────────────────────
  /pith debug            Structured bug diagnosis format
  /pith review           Code review format
  /pith arch             Architecture discussion format
  /pith plan             Step-by-step planning format
  /pith commit           Commit message generation format

── Wiki ────────────────────────────────────────────────────────
  /pith wiki             Toggle wiki mode on/off
  /pith wiki "<q>"       Query the project wiki
  /pith ingest <file>    Add a file/source to the wiki
  /pith lint             Run wiki guard/lint check

── Token & Focus ───────────────────────────────────────────────
  /pith budget <n>       Set hard token limit for responses
  /pith budget off       Clear token budget
  /pith focus <file>     Focus context on a specific file
  /pith symbol <f> <n>   Extract exact lines for a symbol (~95% token saving)
  /pith symbol --list <f> List all symbols in a file

── Session & Config ────────────────────────────────────────────
  /pith status           Token usage health report (with ASCII flow chart)
  /pith report           Generate interactive HTML dashboard (opens in browser)
  /pith hindsight        Identify stale tool results consuming context — recommends /compact
  /pith escalate         Show auto-escalation status and thresholds
  /pith escalate on|off  Enable/disable SWEzze auto-escalation
  /pith recall           Restore last session's mode/wiki/budget
  /pith configure        Interactive config wizard
  /pith tour             7-step interactive guided tour
  /pith tour <1-7>       Jump to a specific tour step
  /pith setup            Reset and re-run onboarding
  /pith optimize-cache   Restructure CLAUDE.md for prompt caching

── Integrations ────────────────────────────────────────────────
  /pith grepai           Show GrepAI semantic search status
  /pith grepai skip      Dismiss "install GrepAI" nudge permanently
  /pith grepai enable    Re-enable the nudge

── Install ─────────────────────────────────────────────────────
  /pith install          Install Pith into this project
  /pith uninstall        Remove Pith from this project

Display this reference any time with: /pith help`
        );

      } else if (arg === 'budget') {
        // /pith budget <n|off> — alias for /budget
        const budgetArg = (rest || '').toLowerCase();
        if (budgetArg === 'off' || budgetArg === '0') {
          saveProjectState({ budget: null });
          out.push('TOKEN BUDGET: cleared.');
        } else {
          const n = parseInt(budgetArg, 10);
          if (!isNaN(n) && n > 0) {
            saveProjectState({ budget: n });
            out.push(`HARD TOKEN LIMIT: ≤${n} tokens this response. Count as you write. Stop when done. No apology for brevity.`);
          } else {
            out.push('[PITH: /pith budget requires a number or "off". Example: /pith budget 150]');
          }
        }

      } else if (arg === 'focus') {
        // /pith focus <file> — alias for /focus
        if (!rest) {
          out.push('[PITH: /pith focus requires a file path. Example: /pith focus src/main.js]');
        } else {
          out.push(runTool('focus.py', [rest, '--question', data.prompt || ''], root));
        }

      } else if (arg === 'symbol' || arg === 'sym') {
        // /pith symbol <file> <name>   — extract exact lines for one symbol
        // /pith symbol --list <file>   — list all symbols in a file
        const subParts = rest.trim().split(/\s+/);
        if (rest.startsWith('--list') || rest.startsWith('-l')) {
          const file = subParts[1] || subParts[0];
          out.push(file ? runTool('symbols.py', [file, '--list'], root)
                        : '[PITH: /pith symbol --list <file>]');
        } else if (subParts.length >= 2) {
          const [file, ...nameParts] = subParts;
          out.push(runTool('symbols.py', [file, nameParts.join(' ')], root));
        } else {
          out.push('[PITH: /pith symbol <file> <symbol_name>  or  /pith symbol --list <file>]');
        }

      } else if (arg === 'grepai') {
        // /pith grepai skip   — permanently dismiss the "install GrepAI" nudge
        // /pith grepai enable — re-enable it (undo skip)
        // /pith grepai status — show current state
        const sub = (rest || '').toLowerCase().trim();
        if (sub === 'skip' || sub === 'dismiss') {
          saveProjectState({ grepai_skip: true });
          out.push(
            'PITH: GrepAI nudge dismissed. Wiki will use keyword search without ' +
            'further prompts.\n' +
            'To re-enable the nudge later: /pith grepai enable\n' +
            'To install GrepAI:            https://github.com/yoanbernabeu/grepai'
          );
        } else if (sub === 'enable') {
          saveProjectState({ grepai_skip: false, grepai_nudge_session: null });
          out.push(
            'PITH: GrepAI nudge re-enabled. Install GrepAI to upgrade wiki search ' +
            'from keyword to semantic.\n' +
            'Install: https://github.com/yoanbernabeu/grepai'
          );
        } else {
          // /pith grepai — show status
          const proj2 = loadProjectState();
          const skipped = proj2.grepai_skip ? 'dismissed (run /pith grepai enable to restore)' : 'active';
          out.push(
            `PITH GREPAI STATUS\n` +
            `  Nudge:   ${skipped}\n` +
            `  Install: https://github.com/yoanbernabeu/grepai\n` +
            `  Commands:\n` +
            `    /pith grepai skip   — stop showing install nudge\n` +
            `    /pith grepai enable — restore nudge`
          );
        }

      } else if (arg === 'reset-cache') {
        // /pith reset-cache — force full session-start injection next session
        saveProjectState({ session_injection_hash: null });
        out.push('PITH: session cache cleared. Full rules will be injected on next session start.');

      } else if (arg === 'report') {
        // /pith report — generate HTML dashboard and open in browser
        out.push(runTool('report.py', [], root));

      } else if (arg === 'hindsight') {
        // /pith hindsight — retrospective stale context analysis
        out.push(runTool('hindsight.py', [], root));
        // Mark as shown so the auto-nudge doesn't fire this turn
        saveProjectState({ hindsight_nudged: true });

      } else if (arg === 'escalate') {
        // /pith escalate on|off — toggle SWEzze auto-escalation
        const sub = (rest || '').toLowerCase().trim();
        if (sub === 'off' || sub === 'disable') {
          saveProjectState({ auto_escalate_disabled: true });
          out.push('PITH: auto-escalation disabled. Output mode will not change automatically as context fills.');
        } else if (sub === 'on' || sub === 'enable') {
          saveProjectState({ auto_escalate_disabled: false });
          out.push('PITH: auto-escalation enabled. Mode will ratchet to LEAN at 50% context, ULTRA at 70%.');
        } else {
          const disabled = loadProjectState().auto_escalate_disabled;
          const fill = Math.round((proj.input_tokens_est || 0) / (config.context_limit || 200000) * 100);
          out.push(
            `PITH AUTO-ESCALATION\n` +
            `  Status:        ${disabled ? 'disabled' : 'enabled'}\n` +
            `  Context fill:  ${fill}%\n` +
            `  Lean at:       ${Math.round((config.escalate_lean_at ?? 0.50) * 100)}%\n` +
            `  Ultra at:      ${Math.round((config.escalate_ultra_at ?? 0.70) * 100)}%\n` +
            `  /pith escalate on|off  — toggle`
          );
        }

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

    // ── SWEzze auto-escalation ─────────────────────────────────────────────
    // Inspired by OCD/SWEzze (arXiv:2603.28119): ratchet output compression
    // as context fills to prevent responses from eating the remaining headroom.
    {
      const esc_proj = loadProjectState();
      if (config.auto_escalate !== false && !esc_proj.auto_escalate_disabled && !lower.startsWith('/pith')) {
        const limit      = config.context_limit || 200000;
        const used       = esc_proj.input_tokens_est || 0;
        const fill       = used / limit;
        const curMode    = esc_proj.mode || config.default_mode || 'off';
        const LEAN_AT    = config.escalate_lean_at  ?? 0.50;
        const ULTRA_AT   = config.escalate_ultra_at ?? 0.70;
        const CEIL_AT    = 0.85;

        if (fill >= ULTRA_AT && curMode !== 'ultra') {
          saveProjectState({
            mode: 'ultra',
            _escalated_from: curMode,
            escalation_count_session: (esc_proj.escalation_count_session || 0) + 1,
          });
          out.push(
            `[PITH AUTO-ESCALATED: context ${Math.round(fill * 100)}% full → ULTRA compression active.\n` +
            ` Previous mode: ${curMode}. Run /pith escalate off to disable.]\n\n` +
            modeRules('ultra', root)
          );
        } else if (fill >= LEAN_AT && (curMode === 'off' || !curMode)) {
          saveProjectState({
            mode: 'lean',
            _escalated_from: curMode,
            escalation_count_session: (esc_proj.escalation_count_session || 0) + 1,
          });
          out.push(
            `[PITH AUTO-ESCALATED: context ${Math.round(fill * 100)}% full → LEAN compression active.\n` +
            ` Run /pith escalate off to disable.]\n\n` +
            modeRules('lean', root)
          );
        }

        // Dynamic response ceiling at 85%+ — cap response to 8% of remaining headroom
        if (fill >= CEIL_AT) {
          const remaining = limit - used;
          const ceiling   = Math.max(80, Math.floor(remaining * 0.08));
          out.push(
            `[PITH: context ${Math.round(fill * 100)}% full — ` +
            `keep this response under ${ceiling} tokens to preserve headroom]`
          );
        }
      }
    }

    // ── Per-message: inject active budget ──────────────────────────────────
    const budget = loadProjectState().budget;
    if (budget && !lower.startsWith('/pith') && !lower.startsWith('/budget') && !lower.startsWith('/focus')) {
      out.push(`[TOKEN LIMIT: ≤${budget} tokens this response]`);
    }

    // ── Token estimate tracking ────────────────────────────────────────────
    const est      = Math.ceil(prompt.length / 4);
    const newTurn  = (proj.turn_count || 0) + 1;
    saveProjectState({ input_tokens_est: (proj.input_tokens_est || 0) + est, turn_count: newTurn });

    // ── Stale result notice ────────────────────────────────────────────────
    // After N turns, remind Claude that large offloaded results are in history
    // but no longer needed — it can ignore them without re-reading.
    const STALE_TURNS  = config.offload_stale_turns || 5;
    const staleResults = (proj.stale_results || []);
    const nowStale     = staleResults.filter(r => (newTurn - r.turn) >= STALE_TURNS);
    if (nowStale.length && !lower.startsWith('/pith')) {
      const list = nowStale.map(r => `  · ${r.label} (${r.tokens}t, turn ${r.turn})`).join('\n');
      out.push(
        `[PITH: ${nowStale.length} old tool result(s) are in your history but offloaded — ` +
        `safe to ignore unless you need them:\n${list}\n` +
        `Use Read("<path>") from the PITH OFFLOAD notice to access if needed]`
      );
    }

    // ── Hindsight auto-nudge ──────────────────────────────────────────────
    // Once per session when context fill > 60%: run hindsight --nudge.
    // Only fires once (hindsight_nudged flag), not on /pith commands.
    {
      const hn_proj = loadProjectState();
      const fill    = (hn_proj.input_tokens_est || 0) / (config.context_limit || 200000);
      if (!hn_proj.hindsight_nudged && fill > 0.60 && !lower.startsWith('/pith')) {
        const nudge = runTool('hindsight.py', ['--nudge'], root);
        if (nudge) {
          out.push(nudge);
          saveProjectState({ hindsight_nudged: true });
        }
      }
    }

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

// Runs a bundled Python tool via execFile (no shell) so user-controlled
// arguments can never be interpreted as shell syntax. `extraArgs` may be an
// array of argv strings or a whitespace-delimited string; both are treated as
// literal argv tokens, never evaluated by a shell.
function runTool(script, args, root, extraArgs) {
  try {
    const toolPath = path.join(root, 'tools', script);
    if (!fs.existsSync(toolPath)) return `[PITH: tool ${script} not found]`;
    const argv = [toolPath, ...args.map(a => String(a))];
    if (extraArgs) {
      const extras = Array.isArray(extraArgs)
        ? extraArgs.map(String)
        : String(extraArgs).split(/\s+/).filter(Boolean);
      argv.push(...extras);
    }
    return execFileSync('python3', argv, {
      timeout: 30000,
      encoding: 'utf8',
      cwd: process.env.CLAUDE_CWD || process.cwd(),
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 8 * 1024 * 1024,
    }).trim();
  } catch (e) {
    const msg = (e.stderr && e.stderr.toString ? e.stderr.toString() : '') || e.message || '';
    return `[PITH: ${script} failed — ${msg.slice(0, 200)}]`;
  }
}

function buildUpdateInstructions(root) {
  // Detect install source: if root contains a .git dir it's the live repo; otherwise ~/.local/share/pith
  const installDir = fs.existsSync(path.join(root, '.git'))
    ? root
    : path.join(require('os').homedir(), '.local', 'share', 'pith');
  const hasGit = fs.existsSync(path.join(installDir, '.git'));

  if (!hasGit) {
    return (
      'PITH UPDATE\n\n' +
      'No git repo found at install location. Re-install to get the latest version:\n\n' +
      '  bash <(curl -s https://raw.githubusercontent.com/abhisekjha/pith/main/install.sh)\n\n' +
      'This will pull the latest version and re-register all hooks.'
    );
  }

  return (
    'PITH UPDATE — run these commands to pull the latest version:\n\n' +
    '```bash\n' +
    `git -C "${installDir}" pull --ff-only\n` +
    `bash "${installDir}/install.sh"\n` +
    '```\n\n' +
    `Install dir: ${installDir}\n` +
    'This pulls the latest commit, re-copies hooks to ~/.claude/hooks/pith/, and re-registers commands.\n\n' +
    'After update, restart Claude Code (or open a new session) for changes to take effect.'
  );
}

function loadTourSkill(root) {
  try {
    return fs.readFileSync(path.join(root, 'skills', 'pith-tour', 'SKILL.md'), 'utf8')
      .replace(/^---[\s\S]*?---\s*/, '');
  } catch (e) {
    return 'PITH TOUR: run the interactive 7-step tour. Guide the user through each Pith feature hands-on.';
  }
}
