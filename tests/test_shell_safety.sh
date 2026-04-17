#!/usr/bin/env bash
# Regression tests for shell-injection fixes (batch 1).
#
# These tests feed crafted user prompts into the hooks and verify that
# shell metacharacters in arguments do NOT get evaluated by a shell.
#
# Run from the repo root: bash tests/test_shell_safety.sh

set -u
cd "$(dirname "$0")/.."

PASS=0
FAIL=0

pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

# ── H-2: runTool no longer invokes a shell ────────────────────────────────────
echo "H-2: prompt-submit.js runTool uses execFile, not shell"

CANARY="$(mktemp -u /tmp/pith_canary_XXXXXX)"
# Use /pith ingest which flows through runTool('ingest.py', [filePath], root).
# If runTool still did shell interpolation, $(…) would create the canary.
PROMPT="/pith ingest \$(touch ${CANARY})"
printf '%s' "{\"prompt\":\"${PROMPT}\"}" | \
  CLAUDE_PLUGIN_ROOT="$(pwd)" node hooks/prompt-submit.js >/dev/null 2>&1 || true

if [ -e "${CANARY}" ]; then
  fail "H-2: canary file was created — shell injection still reachable"
  rm -f "${CANARY}"
else
  pass "H-2: canary file not created — argv-only path confirmed"
fi

# Also try backticks.
CANARY2="$(mktemp -u /tmp/pith_canary2_XXXXXX)"
PROMPT="/pith focus \`touch ${CANARY2}\`"
printf '%s' "{\"prompt\":\"${PROMPT}\"}" | \
  CLAUDE_PLUGIN_ROOT="$(pwd)" node hooks/prompt-submit.js >/dev/null 2>&1 || true

if [ -e "${CANARY2}" ]; then
  fail "H-2: backtick injection reached shell"
  rm -f "${CANARY2}"
else
  pass "H-2: backticks treated literally"
fi

# Semicolon — shouldn't cause command chaining.
CANARY3="$(mktemp -u /tmp/pith_canary3_XXXXXX)"
PROMPT="/pith symbol foo.py bar; touch ${CANARY3}"
printf '%s' "{\"prompt\":\"${PROMPT}\"}" | \
  CLAUDE_PLUGIN_ROOT="$(pwd)" node hooks/prompt-submit.js >/dev/null 2>&1 || true

if [ -e "${CANARY3}" ]; then
  fail "H-2: semicolon-chained command ran"
  rm -f "${CANARY3}"
else
  pass "H-2: semicolon treated literally"
fi

# ── H-3: slash commands — --slash mode accepts raw argv via stdin ─────────────
echo
echo "H-3: prompt-submit.js --slash mode treats stdin as literal"

# Simulate what Claude Code would run for: /pith x'; touch CANARY; echo '
# The heredoc preserves literal single-quote; the hook sees it as an argument.
CANARY4="$(mktemp -u /tmp/pith_canary4_XXXXXX)"
set +e
CLAUDE_PLUGIN_ROOT="$(pwd)" node hooks/prompt-submit.js --slash /pith \
  <<PITH_TEST_EOF >/dev/null 2>&1
x'; touch ${CANARY4}; echo '
PITH_TEST_EOF
set -e

if [ -e "${CANARY4}" ]; then
  fail "H-3: heredoc contents were shell-evaluated"
  rm -f "${CANARY4}"
else
  pass "H-3: single-quote in heredoc body not evaluated"
fi

# --slash prefix validator: refuses injection through the flag itself.
BAD_EXIT=0
echo "" | node hooks/prompt-submit.js --slash '/pith; touch /tmp/bad' >/dev/null 2>&1 || BAD_EXIT=$?
if [ "${BAD_EXIT}" -ne 0 ]; then
  pass "H-3: --slash rejects invalid prefixes"
else
  fail "H-3: --slash accepted a malformed prefix"
fi

# ── L-8: statusline.sh tolerates apostrophe in $HOME ──────────────────────────
echo
echo "L-8: statusline.sh reads state via argv, not string interpolation"

REPO="$(pwd)"
TROUBLE_HOME="$(mktemp -d)/pith_it's"
mkdir -p "${TROUBLE_HOME}/.pith"
KEY=$(printf '%s' "/tmp" | base64 | tr -d '/+=\n' | cut -c1-20)
cat > "${TROUBLE_HOME}/.pith/state.json" <<JSON
{"proj_${KEY}":{"mode":"lean","input_tokens_est":500}}
JSON

set +e
OUT=$(cd /tmp && HOME="${TROUBLE_HOME}" bash "${REPO}/hooks/statusline.sh" 2>&1)
RC=$?
set -e
rm -rf "$(dirname "${TROUBLE_HOME}")"

# The pre-patch statusline would silently fall through to "PITH 0/200k"
# because the apostrophe in $HOME broke the `python3 -c` literal. The fixed
# version must actually read the seeded state and emit "PITH:LEAN 500/200k".
if [ "${RC}" -eq 0 ] && echo "${OUT}" | grep -q 'PITH:LEAN.*500'; then
  pass "L-8: statusline still reads state with apostrophe in \$HOME (${OUT})"
else
  fail "L-8: apostrophe in \$HOME broke state read (rc=${RC}, out=${OUT})"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "── Results ──"
echo "  passed: ${PASS}"
echo "  failed: ${FAIL}"
[ "${FAIL}" -eq 0 ] || exit 1
