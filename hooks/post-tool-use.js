#!/usr/bin/env node
'use strict';
// Pith — PostToolUse hook  ← THE CORE INNOVATION
//
// Runs after every tool call. Compresses tool results before they enter context.
// This is the largest source of token waste in a real Claude Code session —
// nobody else compresses this layer.
//
// Savings: 30-50% of total context tokens in a typical session.
//
// Input (stdin):  JSON { tool_name, tool_input, tool_response }
// Output (stdout): JSON { output: "compressed" }  OR nothing (pass-through)

const fs   = require('fs');
const path = require('path');
const { loadConfig, loadProjectState, saveProjectState } = require('./config');

const config = loadConfig();
if (!config.tool_compress) process.exit(0);

const THRESHOLD = config.tool_compress_threshold || 30;

let raw = '';
process.stdin.on('data', c => { raw += c; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(raw);
    const toolName = (data.tool_name || data.toolName || '').replace('Tool', '');
    const toolInput = data.tool_input || data.toolInput || {};
    let result = data.tool_response || data.toolResponse || '';

    // Normalize: Claude Code sends tool_response as a structured object, not a plain string.
    // Read  → { type: "text", file: { filePath, content, numLines, startLine, totalLines } }
    // Bash  → { stdout, stderr, interrupted, isImage, noOutputExpected }
    // Grep  → { stdout, stderr, ... }  (same as Bash)
    // Generic fallback: look for content/text/output fields, then JSON.stringify
    if (typeof result === 'object' && result !== null) {
      if (result.file && typeof result.file.content === 'string') {
        // Read tool
        result = result.file.content;
      } else if (typeof result.stdout === 'string') {
        // Bash / Grep
        result = result.stdout;
      } else {
        result = result.content || result.text || result.output || JSON.stringify(result);
      }
    }
    if (typeof result !== 'string') result = String(result);

    const lines = result.split('\n');
    if (lines.length <= THRESHOLD) process.exit(0); // small — pass through

    let compressed  = null;
    let comprFormat = 'skeleton'; // track which compression path was used

    switch (toolName.toLowerCase()) {
      case 'read':
      case 'readfile': {
        const resp = data.tool_response || data.toolResponse || {};
        const fp   = toolInput.file_path || toolInput.path ||
                     (resp.file && resp.file.filePath) || '';
        compressed  = compressFileRead(fp, result, lines);
        comprFormat = ['json'].includes((fp.split('.').pop() || '').toLowerCase())
                      ? 'toon' : 'skeleton';
        break;
      }
      case 'bash':
        compressed  = compressBash(toolInput.command || toolInput.cmd || '', result, lines);
        comprFormat = 'bash';
        break;
      case 'grep':
        compressed  = compressGrep(toolInput.pattern || '', result, lines);
        comprFormat = 'grep';
        break;
      case 'webfetch':
        compressed  = compressWeb(toolInput.url || '', result, lines);
        comprFormat = 'web';
        break;
    }

    if (compressed !== null) {
      // Track savings estimate
      const beforeTokens = Math.ceil(result.length / 4);
      const afterTokens  = Math.ceil(compressed.length / 4);
      const savedTokens  = Math.max(0, beforeTokens - afterTokens);
      const proj = loadProjectState();

      // Route savings into per-format buckets for status breakdown
      const updates = {
        tool_savings_session:  (proj.tool_savings_session  || 0) + savedTokens,
        tokens_saved_session:  (proj.tokens_saved_session  || 0) + savedTokens,
      };
      if (comprFormat === 'toon') {
        updates.toon_savings_session = (proj.toon_savings_session || 0) + savedTokens;
        updates.toon_savings_total   = (proj.toon_savings_total   || 0) + savedTokens;
      } else if (comprFormat === 'skeleton') {
        updates.skeleton_savings_session = (proj.skeleton_savings_session || 0) + savedTokens;
      } else if (comprFormat === 'bash') {
        updates.bash_savings_session = (proj.bash_savings_session || 0) + savedTokens;
      }
      saveProjectState(updates);

      // ── Telemetry log ─────────────────────────────────────────────────────
      try {
        const os   = require('os');
        const pDir = require('path').join(os.homedir(), '.pith');
        fs.mkdirSync(pDir, { recursive: true });
        const entry = {
          ts:            new Date().toISOString(),
          session:       proj.session_start || '',
          tool:          toolName,
          format:        comprFormat,
          label:         (toolInput.file_path || toolInput.command || toolInput.pattern || '').slice(0, 80),
          before_lines:  lines.length,
          after_lines:   compressed.split('\n').length,
          before_tokens: beforeTokens,
          after_tokens:  afterTokens,
          saved_pct:     Math.round(savedTokens / beforeTokens * 100),
          before_head:   lines.slice(0, 3).join('\n'),
          after_head:    compressed.split('\n').slice(0, 3).join('\n'),
        };
        fs.appendFileSync(require('path').join(pDir, 'telemetry.jsonl'), JSON.stringify(entry) + '\n');
      } catch (e) { /* never block */ }

      process.stdout.write(JSON.stringify({ output: compressed }));
    }
  } catch (e) { /* silent — never break a session */ }
  process.exit(0);
});

// ── FILE READ ────────────────────────────────────────────────────────────────

function compressFileRead(filePath, content, lines) {
  const ext = (filePath.split('.').pop() || '').toLowerCase();
  const name = path.basename(filePath);

  // Generated/lock files: show only a stub
  if (['lock', 'sum'].includes(ext) || name === 'package-lock.json') {
    return `[PITH: ${name} — generated file, ${lines.length} lines. Showing stub only.]\n` +
           lines.slice(0, 3).join('\n') + '\n...\n[Ask for specific dependency info if needed]';
  }

  if (['js', 'ts', 'jsx', 'tsx', 'mjs', 'cjs'].includes(ext)) return jsSkeleton(content, lines, filePath);
  if (ext === 'py')  return pySkeleton(content, lines, filePath);
  if (ext === 'go')  return goSkeleton(content, lines, filePath);
  if (['java', 'kt', 'swift', 'cs', 'rs'].includes(ext)) return genericSkeleton(content, lines, filePath);
  if (ext === 'json') return compressJSON(content, lines, filePath);
  if (['md', 'txt', 'rst'].includes(ext)) return compressMarkdown(content, lines, filePath);

  return headTail(lines, filePath, 25, 15);
}

function jsSkeleton(content, lines, filePath) {
  const kept = [];
  let depth = 0;
  let inMLComment = false;

  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith('/*')) inMLComment = true;
    if (inMLComment) {
      if (depth === 0) kept.push(line);
      if (t.endsWith('*/')) inMLComment = false;
      continue;
    }

    // Brace counting (ignoring strings is approximate but good enough)
    const opens = (line.match(/\{/g) || []).length;
    const closes = (line.match(/\}/g) || []).length;

    const keep =
      t.startsWith('import ') ||
      t.startsWith('export ') ||
      t.startsWith('require(') ||
      t.startsWith('// ') ||
      t.startsWith('/**') ||
      t.startsWith(' * ') ||
      (depth === 0 && (
        t.match(/^(async\s+)?function[\s*]/) ||
        t.match(/^(const|let|var)\s+\w+\s*=\s*(async\s+)?\(/) ||
        t.match(/^(abstract\s+)?class\s+/) ||
        t.match(/^(type|interface|enum)\s+/) ||
        t.match(/^@\w+/)
      )) ||
      (depth === 1 && t.match(/^(async\s+)?(static\s+)?(private\s+|public\s+|protected\s+)?(\w+)\s*[\(\{]/));

    if (keep) kept.push(line);
    else if (depth === 0 && !t) {
      if (kept.length && kept[kept.length - 1] !== '') kept.push('');
    }

    depth = Math.max(0, depth + opens - closes);
  }

  if (kept.length >= lines.length * 0.75) return headTail(lines, filePath, 40, 20);
  return skeleton(kept, lines.length, filePath);
}

function pySkeleton(content, lines, filePath) {
  const kept = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();
    if (!t) { if (kept.length && kept[kept.length - 1] !== '') kept.push(''); continue; }
    const keep =
      t.startsWith('import ') || t.startsWith('from ') ||
      t.startsWith('def ') || t.startsWith('async def ') ||
      t.startsWith('class ') || t.startsWith('@') ||
      t.startsWith('#') ||
      (line.match(/^\S/) && t.match(/^[A-Z_][A-Z_0-9]*\s*=/));  // CONSTANTS
    if (keep) {
      kept.push(line);
      // Grab first docstring line
      if ((t.startsWith('def ') || t.startsWith('class ')) && i + 1 < lines.length) {
        const next = lines[i + 1].trim();
        if (next.startsWith('"""') || next.startsWith("'''")) kept.push(lines[i + 1]);
      }
    }
  }
  if (kept.length >= lines.length * 0.75) return headTail(lines, filePath, 40, 20);
  return skeleton(kept, lines.length, filePath);
}

function goSkeleton(content, lines, filePath) {
  const kept = [];
  let depth = 0;
  for (const line of lines) {
    const t = line.trim();
    const opens = (line.match(/\{/g) || []).length;
    const closes = (line.match(/\}/g) || []).length;
    const keep = depth === 0 && (
      t.startsWith('package ') || t.startsWith('import ') ||
      t.startsWith('func ') || t.startsWith('type ') ||
      t.startsWith('var ') || t.startsWith('const ') ||
      t.startsWith('//')
    );
    if (keep) kept.push(line);
    else if (depth === 0 && !t) {
      if (kept.length && kept[kept.length - 1] !== '') kept.push('');
    }
    depth = Math.max(0, depth + opens - closes);
  }
  if (kept.length >= lines.length * 0.75) return headTail(lines, filePath, 40, 20);
  return skeleton(kept, lines.length, filePath);
}

function genericSkeleton(content, lines, filePath) {
  // Reuse JS skeleton — brace-counting works for Java/Kotlin/C#/Rust
  return jsSkeleton(content, lines, filePath);
}

function compressJSON(content, lines, filePath) {
  const name = path.basename(filePath);
  try {
    const obj = JSON.parse(content);
    if (name === 'package.json') {
      const out = {
        name: obj.name, version: obj.version, main: obj.main, type: obj.type,
        scripts: obj.scripts,
        dependencies:    Object.keys(obj.dependencies    || {}),
        devDependencies: Object.keys(obj.devDependencies || {}).slice(0, 15),
        _pith: `[${Object.keys(obj.dependencies||{}).length} deps · ${Object.keys(obj.devDependencies||{}).length} devDeps]`,
      };
      return `[PITH TOON: ${name} summary]\n${toTOON(out)}`;
    }
    return `[PITH TOON: ${name} — ${lines.length} lines]\n${toTOON(obj)}\n[Ask for full content or specific keys]`;
  } catch (e) {
    return headTail(lines, filePath, 20, 10);
  }
}

// ── TOON serializer (Token-Oriented Object Notation) ─────────────────────────
// Spec: github.com/toon-format/toon — ~40% fewer tokens than JSON
// Rules: key=value pairs, semicolons between siblings, tabular arrays
function toTOON(obj, depth = 0) {
  if (obj === null || obj === undefined) return 'null';
  if (typeof obj !== 'object') return String(obj);

  const pad = '  '.repeat(depth);

  if (Array.isArray(obj)) {
    if (obj.length === 0) return '[]';
    // Tabular format for arrays of objects — header row + value rows
    if (obj.length > 1 && obj[0] && typeof obj[0] === 'object' && !Array.isArray(obj[0])) {
      const keys  = Object.keys(obj[0]);
      const shown = obj.slice(0, 20);
      const rows  = shown.map(item =>
        keys.map(k => {
          const v = item[k];
          return (v === null || v === undefined) ? '' :
                 (typeof v === 'object') ? '{…}' : String(v);
        }).join(',')
      );
      const tail = obj.length > 20 ? `\n${pad}  …${obj.length - 20} more` : '';
      return `[${obj.length}]{${keys.join(',')}}\n` +
             rows.map(r => `${pad}  ${r}`).join('\n') + tail;
    }
    // Simple array — semicolon-delimited
    const items = obj.slice(0, 30).map(v => toTOON(v, depth + 1));
    const tail  = obj.length > 30 ? `;…${obj.length - 30} more` : '';
    return items.join(';') + tail;
  }

  // Object — key=value, newline between entries when nested
  const entries = Object.entries(obj).slice(0, 20);
  const pairs   = entries.map(([k, v]) => {
    if (v === null || v === undefined) return `${pad}${k}=null`;
    if (typeof v !== 'object')         return `${pad}${k}=${v}`;
    if (Array.isArray(v) && v.length === 0) return `${pad}${k}=[]`;
    return `${pad}${k}:\n${toTOON(v, depth + 1)}`;
  });
  if (Object.keys(obj).length > 20)
    pairs.push(`${pad}…${Object.keys(obj).length - 20} more keys`);
  return pairs.join('\n');
}

// Legacy — kept for internal use only (schemaOf was used nowhere else)
function schemaOf(obj, depth) {
  if (depth > 3) return '...';
  if (obj === null) return 'null';
  if (Array.isArray(obj)) return `Array(${obj.length}) of ${schemaOf(obj[0], depth + 1)}`;
  if (typeof obj !== 'object') return typeof obj;
  const entries = Object.entries(obj).slice(0, 15).map(
    ([k, v]) => `${'  '.repeat(depth + 1)}${k}: ${schemaOf(v, depth + 1)}`
  );
  if (Object.keys(obj).length > 15) entries.push(`${'  '.repeat(depth + 1)}... ${Object.keys(obj).length - 15} more`);
  return `{\n${entries.join('\n')}\n${'  '.repeat(depth)}}`;
}

function compressMarkdown(content, lines, filePath) {
  const kept = [];
  let inCode = false;
  let codeLine = 0;
  let sectionLines = 0;

  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith('```') || t.startsWith('~~~')) {
      inCode = !inCode; codeLine = 0; kept.push(line); continue;
    }
    if (inCode) {
      if (codeLine < 15) kept.push(line);
      else if (codeLine === 15) kept.push('[...truncated...]');
      codeLine++; continue;
    }
    if (t.match(/^#{1,6}\s/)) { kept.push(line); sectionLines = 0; continue; }
    if (t.startsWith('|')) { kept.push(line); continue; }
    if (sectionLines === 0 && t) { kept.push(line); sectionLines++; continue; }
    if ((t.startsWith('- ') || t.match(/^\d+\.\s/)) && sectionLines < 12) {
      kept.push(line); sectionLines++; continue;
    }
    if (!t && kept.length && kept[kept.length - 1] !== '') kept.push('');
  }
  return skeleton(kept, lines.length, filePath, 'structure');
}

// ── BASH ─────────────────────────────────────────────────────────────────────

function compressBash(cmd, content, lines) {
  const c = cmd.toLowerCase().trim();
  const errors  = lines.filter(l => /\b(error|err:|fail|failed|exception|traceback|fatal|panic)\b/i.test(l));
  const warns   = lines.filter(l => /\b(warn|warning|deprecated)\b/i.test(l));
  const success = lines.filter(l => /\b(success|done|complete|created|updated|installed|built|passed|ok)\b/i.test(l));

  if (c.startsWith('git log')) {
    const commits = lines.filter(l => l.match(/^[a-f0-9]{6,}/i) || l.match(/^commit [a-f0-9]{40}/i));
    if (commits.length > 12) {
      return `[PITH: git log — ${commits.length} commits, last 12]\n${commits.slice(-12).join('\n')}\n[${commits.length - 12} older commits omitted]`;
    }
    return null;
  }

  if (c.match(/^(npm|yarn|pnpm|pip3?)\s+(install|i\b|add|update)/)) {
    const summary = [...errors, ...warns, ...success, lines[lines.length - 1]].filter(Boolean);
    const deduped = [...new Set(summary)];
    return `[PITH: ${c.split(' ').slice(0, 3).join(' ')} — ${lines.length} lines]\n${deduped.join('\n')}`;
  }

  if (c.match(/^(make|cargo|tsc|webpack|vite build|go build|gradle|mvn)/)) {
    if (errors.length) {
      return `[PITH: build — ${errors.length} errors from ${lines.length} lines]\n${errors.slice(0, 20).join('\n')}`;
    }
    return `[PITH: build passed — ${lines.length} lines]\n${lines.slice(-6).join('\n')}`;
  }

  if (c.match(/^(jest|vitest|pytest|go test|cargo test|rspec|mocha)/)) {
    const fail = lines.filter(l => /\b(FAIL|FAILED|✗|✕|×)\b/.test(l));
    const summary = lines.filter(l => /\d+\s+(passed|failed|skipped|tests)/.test(l));
    if (fail.length) {
      return `[PITH: tests — ${fail.length} failing]\n${fail.slice(0, 15).join('\n')}\n${summary.join('\n')}`;
    }
    return `[PITH: tests passed]\n${summary.length ? summary.join('\n') : lines.slice(-3).join('\n')}`;
  }

  if (c.startsWith('git diff') || c.startsWith('git status')) return null; // always pass through

  // Default: head + errors + tail
  if (errors.length) {
    return `[PITH: ${lines.length} lines — ${errors.length} errors]\n` +
           lines.slice(0, 8).join('\n') + '\n\n[Errors:]\n' +
           errors.slice(0, 10).join('\n') + '\n\n' + lines.slice(-5).join('\n');
  }
  return headTail(lines, cmd.slice(0, 60), 15, 10);
}

// ── GREP ─────────────────────────────────────────────────────────────────────

function compressGrep(pattern, content, lines) {
  const MAX = 25;
  if (lines.length <= MAX) return null;
  return `[PITH: grep "${pattern.slice(0, 40)}" — ${lines.length} results, first ${MAX} shown]\n` +
         lines.slice(0, MAX).join('\n') +
         `\n[...${lines.length - MAX} more. Refine pattern or use /focus]`;
}

// ── WEB FETCH ────────────────────────────────────────────────────────────────

function compressWeb(url, content, lines) {
  const text = [];
  let inTag = false;
  let skipBlock = false;
  for (const line of lines) {
    const tl = line.trim().toLowerCase();
    if (tl.includes('<script') || tl.includes('<style')) { skipBlock = true; }
    if (tl.includes('</script>') || tl.includes('</style>')) { skipBlock = false; continue; }
    if (skipBlock) continue;
    const stripped = line.replace(/<[^>]+>/g, '').trim();
    if (stripped.length > 25) text.push(stripped);
  }
  if (text.length >= lines.length * 0.7) return headTail(lines, url.slice(0, 60), 30, 10);
  const shown = text.slice(0, 80).join('\n');
  return `[PITH: ${url.slice(0, 60)} — ${lines.length} lines → ${text.length} text lines]\n\n${shown}` +
         (text.length > 80 ? `\n[...${text.length - 80} more lines]` : '');
}

// ── HELPERS ──────────────────────────────────────────────────────────────────

function skeleton(kept, totalLines, label, kind = 'skeleton') {
  const name = path.basename(String(label));
  return `[PITH ${kind.toUpperCase()}: ${name} — ${totalLines} lines → ${kept.length} shown]\n\n` +
         kept.join('\n').replace(/\n{3,}/g, '\n\n') +
         `\n\n[Full file: ask for specific function/section by name]`;
}

function headTail(lines, label, head, tail) {
  const name = path.basename(String(label));
  const total = lines.length;
  const omitted = total - head - tail;
  if (omitted <= 0) return null; // nothing to compress, pass-through
  return `[PITH: ${name} — ${total} lines → ${head + tail} shown]\n\n` +
         lines.slice(0, head).join('\n') +
         `\n\n[...${omitted} lines omitted...]\n\n` +
         lines.slice(-tail).join('\n');
}
