#!/usr/bin/env bash
# Pith statusline wrapper — composes the PITH badge with whatever statusline
# the user had configured before Pith was installed.
#
# Claude Code's `settings.statusLine` only supports a single command. To avoid
# clobbering a user-provided statusline, the installer saves the previous
# command into ~/.config/pith/config.json under `original_statusline.command`
# and points `statusLine.command` at THIS wrapper instead. The wrapper:
#
#   1. Buffers the session JSON from stdin (Claude Code's statusline contract).
#   2. Runs Pith's own badge (statusline.sh, no stdin — reads ~/.pith/state.json).
#   3. Re-runs the saved original command with the buffered stdin.
#   4. Prefixes our badge onto the first line of the original output:
#          "PITH … · <first line of original>"
#          "<remaining original lines>"
#
# If no original is saved, behaviour is identical to statusline.sh alone.
# Errors in either command are swallowed — a statusline must never fail the UI.
#
# The command stored in original_statusline.command is the same command the
# user already allowed Claude Code to execute via settings.statusLine, so
# running it here introduces no new attack surface — provided the config
# file itself hasn't been tampered with. Before honouring the saved command
# we verify the config is a regular file, opened without following symlinks,
# owned by us, and not group/other-writable. If any check fails the saved
# command is ignored and the wrapper falls back to badge-only output.

CONFIG="${HOME}/.config/pith/config.json"
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

input="$(cat || true)"

pith="$(bash "${HOOKS_DIR}/statusline.sh" 2>/dev/null || printf 'PITH')"

orig="$(
  python3 - "$CONFIG" <<'PY' 2>/dev/null
import json, os, stat, sys
path = sys.argv[1]
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except Exception:
    sys.exit(0)
try:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        sys.exit(0)
    if st.st_uid != os.geteuid():
        sys.exit(0)
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        sys.exit(0)
    with os.fdopen(fd, "r") as f:
        fd = -1
        c = json.load(f)
finally:
    if fd != -1:
        try: os.close(fd)
        except Exception: pass
o = c.get("original_statusline") or {}
cmd = o.get("command") if isinstance(o, dict) else None
if isinstance(cmd, str) and cmd:
    print(cmd)
PY
)"

if [ -z "$orig" ]; then
  printf '%s\n' "$pith"
  exit 0
fi

orig_out="$(printf '%s' "$input" | bash -c "$orig" 2>/dev/null)"

first="$(printf '%s\n' "$orig_out" | sed -n '1p')"
rest="$(printf '%s\n'  "$orig_out" | sed -n '2,$p')"

if [ -n "$first" ]; then
  printf '%s · %s\n' "$pith" "$first"
else
  printf '%s\n' "$pith"
fi
[ -n "$rest" ] && printf '%s\n' "$rest"
