#!/usr/bin/env bash
# Regression tests for hooks/statusline-wrapper.sh.
#
# The wrapper composes the PITH badge with a user-configured statusline that
# the installer preserved into ~/.config/pith/config.json. This file locks in:
#
#   - passthrough when no original is saved (identical to statusline.sh)
#   - composition: badge · first-line + remaining original lines preserved
#   - safety: group/world-writable config is ignored (no command is run)
#
# Run from the repo root: bash tests/test_statusline_wrapper.sh

set -u
cd "$(dirname "$0")/.."

REPO="$(pwd)"
WRAPPER="${REPO}/hooks/statusline-wrapper.sh"

PASS=0
FAIL=0
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

# Every test gets a clean HOME with a seeded pith state so statusline.sh
# produces a deterministic "PITH:LEAN 500/200k" badge.
fresh_home() {
  local h
  h="$(mktemp -d)"
  mkdir -p "${h}/.pith" "${h}/.config/pith"
  local key
  key=$(printf '%s' "/tmp" | base64 | tr -d '/+=\n' | cut -c1-20)
  cat > "${h}/.pith/state.json" <<JSON
{"proj_${key}":{"mode":"lean","input_tokens_est":500}}
JSON
  printf '%s' "$h"
}

# ── Passthrough: no saved original ────────────────────────────────────────────
echo "passthrough: no original_statusline → wrapper output == statusline.sh output"
H="$(fresh_home)"
printf '{}' > "${H}/.config/pith/config.json"
chmod 600 "${H}/.config/pith/config.json"

WRAPPED="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" </dev/null 2>&1)"
PLAIN="$(  cd /tmp && HOME="${H}" bash "${REPO}/hooks/statusline.sh" </dev/null 2>&1)"
rm -rf "${H}"

if [ "${WRAPPED}" = "${PLAIN}" ] && echo "${WRAPPED}" | grep -q 'PITH:LEAN.*500'; then
  pass "wrapper is a no-op passthrough for statusline.sh (${WRAPPED})"
else
  fail "expected wrapper to equal plain statusline.sh; wrapped=<${WRAPPED}> plain=<${PLAIN}>"
fi

# ── Single-line composition ───────────────────────────────────────────────────
echo
echo "single-line: original emits one line → 'PITH · <line>'"
H="$(fresh_home)"
cat > "${H}/.config/pith/config.json" <<JSON
{"original_statusline":{"type":"command","command":"printf '%s' 'user-line-ONE'"}}
JSON
chmod 600 "${H}/.config/pith/config.json"

OUT="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" </dev/null 2>&1)"
rm -rf "${H}"

if echo "${OUT}" | grep -q 'PITH:LEAN.*· user-line-ONE'; then
  pass "single-line composed (${OUT})"
else
  fail "single-line compose failed; got <${OUT}>"
fi

# ── Multi-line composition ────────────────────────────────────────────────────
echo
echo "multi-line: first line composed, remaining lines preserved verbatim"
H="$(fresh_home)"
cat > "${H}/.config/pith/config.json" <<'JSON'
{"original_statusline":{"type":"command","command":"printf '%s\\n%s\\n%s' 'line-one' 'line-two' 'line-three'"}}
JSON
chmod 600 "${H}/.config/pith/config.json"

OUT="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" </dev/null 2>&1)"
rm -rf "${H}"

FIRST="$(printf '%s\n' "${OUT}" | sed -n '1p')"
REST="$(printf '%s\n' "${OUT}"  | sed -n '2,$p')"

ok=1
echo "${FIRST}" | grep -q 'PITH:LEAN.*· line-one' || ok=0
[ "${REST}" = "$(printf 'line-two\nline-three')" ] || ok=0
if [ "${ok}" -eq 1 ]; then
  pass "multi-line composed; rest preserved"
else
  fail "multi-line compose wrong; first=<${FIRST}> rest=<${REST}>"
fi

# ── Wrapper feeds session JSON stdin to the original ──────────────────────────
echo
echo "stdin: original receives the session JSON the wrapper was given"
H="$(fresh_home)"
cat > "${H}/.config/pith/config.json" <<'JSON'
{"original_statusline":{"type":"command","command":"cat"}}
JSON
chmod 600 "${H}/.config/pith/config.json"

OUT="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" <<<'{"session_id":"abc123"}' 2>&1)"
rm -rf "${H}"

if echo "${OUT}" | grep -q 'PITH:LEAN.*· {"session_id":"abc123"}'; then
  pass "stdin forwarded to original command"
else
  fail "stdin not forwarded; got <${OUT}>"
fi

# ── Safety: group-writable config is ignored ──────────────────────────────────
echo
echo "safety: group-writable config is ignored (no command executed)"
H="$(fresh_home)"
CANARY="$(mktemp -u /tmp/pith_wrapper_canary_XXXXXX)"
cat > "${H}/.config/pith/config.json" <<JSON
{"original_statusline":{"type":"command","command":"touch ${CANARY}; printf HIJACK"}}
JSON
chmod 660 "${H}/.config/pith/config.json"   # group-writable → must be rejected

OUT="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" </dev/null 2>&1)"
rm -rf "${H}"

if [ ! -e "${CANARY}" ] && ! echo "${OUT}" | grep -q 'HIJACK'; then
  pass "group-writable config refused; badge-only output"
else
  fail "group-writable config honoured; canary=${CANARY} out=<${OUT}>"
  rm -f "${CANARY}"
fi

# ── Safety: world-writable config is ignored ──────────────────────────────────
echo
echo "safety: world-writable config is ignored"
H="$(fresh_home)"
CANARY="$(mktemp -u /tmp/pith_wrapper_canary_XXXXXX)"
cat > "${H}/.config/pith/config.json" <<JSON
{"original_statusline":{"type":"command","command":"touch ${CANARY}; printf HIJACK"}}
JSON
chmod 606 "${H}/.config/pith/config.json"   # world-writable → must be rejected

OUT="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" </dev/null 2>&1)"
rm -rf "${H}"

if [ ! -e "${CANARY}" ] && ! echo "${OUT}" | grep -q 'HIJACK'; then
  pass "world-writable config refused; badge-only output"
else
  fail "world-writable config honoured; canary=${CANARY} out=<${OUT}>"
  rm -f "${CANARY}"
fi

# ── Safety: symlinked config is ignored (O_NOFOLLOW) ──────────────────────────
echo
echo "safety: symlinked config is ignored"
H="$(fresh_home)"
CANARY="$(mktemp -u /tmp/pith_wrapper_canary_XXXXXX)"
REAL="$(mktemp)"
cat > "${REAL}" <<JSON
{"original_statusline":{"type":"command","command":"touch ${CANARY}; printf HIJACK"}}
JSON
chmod 600 "${REAL}"
rm -f "${H}/.config/pith/config.json"
ln -s "${REAL}" "${H}/.config/pith/config.json"

OUT="$(cd /tmp && HOME="${H}" bash "${WRAPPER}" </dev/null 2>&1)"
rm -rf "${H}" "${REAL}"

if [ ! -e "${CANARY}" ] && ! echo "${OUT}" | grep -q 'HIJACK'; then
  pass "symlinked config refused (O_NOFOLLOW)"
else
  fail "symlinked config honoured; canary=${CANARY} out=<${OUT}>"
  rm -f "${CANARY}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "── Results ──"
echo "  passed: ${PASS}"
echo "  failed: ${FAIL}"
[ "${FAIL}" -eq 0 ] || exit 1
