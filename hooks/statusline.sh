#!/usr/bin/env bash
# Pith statusline — fast badge for Claude Code statusline.
# Output: "PITH 12k/200k" or "PITH:LEAN 12k/200k" or "PITH:WIKI 12k/200k"
#
# Single python3 invocation per keypress (down from three). The state path
# and project key are passed as argv — never interpolated into `python3 -c`
# literals — so unusual $HOME / cwd characters can't break the script.

STATE="${HOME}/.pith/state.json"
[ ! -f "$STATE" ] && echo "PITH" && exit 0

# Derive project key from cwd. Base64 + strip non-alnum keeps this safe to
# pass as argv (no quoting hazard) and reproducible.
CWD_KEY=$(printf '%s' "$PWD" | base64 | tr -d '/+=\n' | cut -c1-20)
PROJ_KEY="proj_${CWD_KEY}"

# Single python call returns a TAB-separated tuple: mode, wiki, tokens, pct.
# If anything goes wrong it prints safe defaults so the badge still renders.
read -r MODE WIKI TOKENS PCT < <(
  python3 - "$STATE" "$PROJ_KEY" <<'PY' 2>/dev/null || printf 'off\t0\t0\t0\n'
import json, sys
try:
    state_path, key = sys.argv[1], sys.argv[2]
    with open(state_path) as f:
        d = json.load(f)
    proj = d.get(key) or {}
    mode = proj.get("mode") or "off"
    wiki = "1" if proj.get("wiki_mode") else "0"
    tokens = int(proj.get("input_tokens_est", 0) or 0)
    saved  = int(proj.get("tool_savings_session", 0) or 0)
    total  = tokens + saved
    pct    = round(saved / total * 100) if total > 0 else 0
    print(f"{mode}\t{wiki}\t{tokens}\t{pct}")
except Exception:
    print("off\t0\t0\t0")
PY
)

# Defaults for read's output (if the here-doc produced nothing at all).
MODE="${MODE:-off}"
WIKI="${WIKI:-0}"
TOKENS="${TOKENS:-0}"
PCT="${PCT:-0}"

# Format token count (e.g. 12345 → 12k).
if [ "${TOKENS}" -ge 1000 ] 2>/dev/null; then
  TOKENS_FMT="$(( TOKENS / 1000 ))k"
else
  TOKENS_FMT="${TOKENS}"
fi

# Build badge suffix: wiki > mode > nothing.
if [ "$WIKI" = "1" ]; then
  SUFFIX=":WIKI"
elif [ "$MODE" != "off" ] && [ -n "$MODE" ]; then
  SUFFIX=":$(printf '%s' "$MODE" | tr '[:lower:]' '[:upper:]')"
else
  SUFFIX=""
fi

if [ "${PCT}" -gt 0 ] 2>/dev/null; then
  echo "PITH${SUFFIX} ↓${PCT}% ${TOKENS_FMT}/200k"
else
  echo "PITH${SUFFIX} ${TOKENS_FMT}/200k"
fi
