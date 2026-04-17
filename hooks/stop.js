#!/usr/bin/env node
'use strict';
// Pith — Stop hook
// Runs when Claude finishes a response.
// Reads transcript JSONL for exact usage counts; falls back to response-length estimate.

const fs = require('fs');
const { loadProjectState, saveProjectState } = require('./config');

// Read token counts from transcript JSONL.
// output = sum of all turns (each turn's output is independent)
// input  = latest assistant entry only (each turn's input includes full history → summing double-counts)
function readTranscriptTokens(transcriptPath) {
  let outputTokens = 0, latestInputTokens = 0;
  try {
    const lines = fs.readFileSync(transcriptPath, 'utf8').split('\n');
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const d = JSON.parse(line);
        if (d.type === 'assistant' && d.message && d.message.usage) {
          const u = d.message.usage;
          outputTokens += u.output_tokens || 0;
          latestInputTokens = (u.input_tokens || 0)
                            + (u.cache_read_input_tokens || 0)
                            + (u.cache_creation_input_tokens || 0);
        }
      } catch (_) { /* skip malformed line */ }
    }
  } catch (_) { /* file unreadable — caller falls back */ }
  return { outputTokens, inputTokens: latestInputTokens };
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
    // stop.js fires once per response. actualOut = cumulative session total from transcript.
    // Must compute savings on DELTA only (new output this turn) to avoid compounding.
    // deltaOut = cumulative now minus cumulative at last stop event.
    const IN_COST_PER_M  = 3.0;
    const OUT_COST_PER_M = 15.0;

    if (actualOut > 0) {
      const prevOut  = proj.output_tokens_last_stop || 0;
      const deltaOut = Math.max(0, actualOut - prevOut);
      updates.output_tokens_last_stop = actualOut;

      // Track turn count for per-response averages
      updates.turn_count_session = (proj.turn_count_session || 0) + 1;

      const mode = proj.mode || 'off';
      const rate = mode === 'ultra' ? 0.42 : mode === 'lean' ? 0.25 : mode === 'precise' ? 0.12 : 0;
      if (rate > 0 && deltaOut > 0) {
        const baseline = Math.ceil(deltaOut / (1 - rate));
        const outSaved = baseline - deltaOut;
        updates.output_savings_session = (proj.output_savings_session || 0) + outSaved;
      }
    }

    // Accumulate lifetime totals
    const sessionSaved = proj.tokens_saved_session || 0;
    updates.tokens_saved_total = (proj.tokens_saved_total || 0) + sessionSaved;

    // Lifetime cost saved — split by token type
    const outSavedSession = updates.output_savings_session || proj.output_savings_session || 0;
    const toolSaved       = Math.max(0, sessionSaved - outSavedSession);
    const sessionCostSaved = (toolSaved        / 1_000_000 * IN_COST_PER_M)
                           + (outSavedSession   / 1_000_000 * OUT_COST_PER_M);
    updates.cost_saved_total = (proj.cost_saved_total || 0) + sessionCostSaved;

    saveProjectState(updates);
  } catch (e) { /* silent */ }
  process.exit(0);
});
