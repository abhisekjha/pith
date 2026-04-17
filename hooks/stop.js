#!/usr/bin/env node
'use strict';
// Pith — Stop hook
// Runs when Claude finishes a response.
// Reads transcript JSONL for exact usage counts; falls back to response-length estimate.

const fs = require('fs');
const { loadProjectState, saveProjectState } = require('./config');

// Sum output_tokens and input_tokens from all assistant entries in a transcript JSONL.
function readTranscriptTokens(transcriptPath) {
  let outputTokens = 0, inputTokens = 0;
  try {
    const lines = fs.readFileSync(transcriptPath, 'utf8').split('\n');
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const d = JSON.parse(line);
        if (d.type === 'assistant' && d.message && d.message.usage) {
          const u = d.message.usage;
          outputTokens += u.output_tokens || 0;
          inputTokens  += (u.input_tokens || 0)
                        + (u.cache_read_input_tokens || 0)
                        + (u.cache_creation_input_tokens || 0);
        }
      } catch (_) { /* skip malformed line */ }
    }
  } catch (_) { /* file unreadable — caller falls back */ }
  return { outputTokens, inputTokens };
}

let raw = '';
process.stdin.on('data', c => { raw += c; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(raw);
    const proj = loadProjectState();
    const updates = {};

    // ── Token counts: transcript > data.usage > response-length estimate ──
    let actualOut = 0;
    if (data.transcript_path) {
      const { outputTokens, inputTokens } = readTranscriptTokens(data.transcript_path);
      if (outputTokens > 0 || inputTokens > 0) {
        actualOut = outputTokens;
        updates.output_tokens_est    = outputTokens;
        updates.input_tokens_est     = inputTokens;
        updates.output_tokens_actual = outputTokens;
        updates.input_tokens_actual  = inputTokens;
      }
    }

    if (actualOut === 0 && data.usage) {
      updates.input_tokens_actual  = (proj.input_tokens_actual  || 0) + (data.usage.input_tokens  || 0);
      updates.output_tokens_actual = (proj.output_tokens_actual || 0) + (data.usage.output_tokens || 0);
      actualOut = data.usage.output_tokens || 0;
      updates.input_tokens_est = updates.input_tokens_actual;
    }

    if (actualOut === 0 && data.response) {
      // Last-resort estimate from response text length
      actualOut = Math.ceil(String(data.response).length / 4);
      updates.output_tokens_est = (proj.output_tokens_est || 0) + actualOut;
      updates.input_tokens_est  = (proj.input_tokens_est  || 0) + actualOut;
    }

    // ── Output savings from active compression mode ───────────────────────
    // When lean/ultra is active, Claude writes shorter responses.
    // Savings = (what output would have been without mode) - actual output.
    // Baseline estimate: actual / (1 - compression_rate)
    // Rates are conservative estimates validated against OCD/SWEzze benchmarks.
    if (actualOut > 0) {
      const mode = proj.mode || 'off';
      const rate = mode === 'ultra' ? 0.42 : mode === 'lean' ? 0.25 : mode === 'precise' ? 0.12 : 0;
      if (rate > 0) {
        const baseline  = Math.ceil(actualOut / (1 - rate));
        const outSaved  = baseline - actualOut;
        updates.output_savings_session = (proj.output_savings_session || 0) + outSaved;
      }
    }

    // Accumulate lifetime totals
    const sessionSaved = proj.tokens_saved_session || 0;
    updates.tokens_saved_total = (proj.tokens_saved_total || 0) + sessionSaved;

    // Lifetime cost saved — split by token type (input vs output rate)
    const IN_COST_PER_M  = 3.0;   // Sonnet 4.6 input
    const OUT_COST_PER_M = 15.0;  // Sonnet 4.6 output
    const outSaved       = (proj.output_savings_session || 0) + (updates.output_savings_session
                           ? (updates.output_savings_session - (proj.output_savings_session || 0)) : 0);
    const toolSaved      = Math.max(0, sessionSaved - outSaved);
    const sessionCostSaved = (toolSaved  / 1_000_000 * IN_COST_PER_M)
                           + (outSaved   / 1_000_000 * OUT_COST_PER_M);
    updates.cost_saved_total = (proj.cost_saved_total || 0) + sessionCostSaved;

    saveProjectState(updates);
  } catch (e) { /* silent */ }
  process.exit(0);
});
