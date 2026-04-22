#!/usr/bin/env bash
set -e

CLAUDE_DIR="${HOME}/.claude"
HOOKS_DIR="${CLAUDE_DIR}/hooks/pith"
SETTINGS="${CLAUDE_DIR}/settings.json"

echo ""
echo "Uninstalling Pith..."

# Remove hooks directory
if [ -d "${HOOKS_DIR}" ]; then
  rm -rf "${HOOKS_DIR}"
  echo "  ✓ hooks removed"
fi

# Remove slash commands
for cmd in pith budget focus pith-graph; do
  f="${CLAUDE_DIR}/commands/${cmd}.md"
  if [ -f "${f}" ]; then
    rm "${f}"
    echo "  ✓ /${cmd} command removed"
  fi
done

# Patch settings.json — remove pith hooks and restore any pre-Pith statusline.
if [ -f "${SETTINGS}" ]; then
  PITH_CONFIG="${HOME}/.config/pith/config.json"
  node - "${SETTINGS}" "${PITH_CONFIG}" "${HOOKS_DIR}" <<'NODESCRIPT'
const fs = require('fs');
const p           = process.argv[2];
const pithCfgPath = process.argv[3];
const hooksDir    = process.argv[4];
let s = {};
try { s = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) {}

// Remove pith hooks from all hook events
if (s.hooks) {
  for (const event of Object.keys(s.hooks)) {
    s.hooks[event] = (s.hooks[event] || []).filter(
      entry => !JSON.stringify(entry).includes('pith')
    );
    if (s.hooks[event].length === 0) delete s.hooks[event];
  }
  if (Object.keys(s.hooks).length === 0) delete s.hooks;
}

// Decide whether a statusLine entry is ours. Matching "pith" anywhere in the
// serialised object would false-positive on a user command that happens to
// contain the substring (e.g. a prompt printing "pithy"), so we scope the
// check to `command` and require it to point at one of our two scripts.
function isPithStatusLine(statusLine) {
  if (!statusLine || typeof statusLine !== 'object') return false;
  const command = statusLine.command;
  if (typeof command !== 'string') return false;
  return (
    command.includes(`${hooksDir}/statusline.sh`) ||
    command.includes(`${hooksDir}/statusline-wrapper.sh`)
  );
}

// Restore the user's pre-Pith statusline if we saved one; otherwise strip
// ours entirely. Only touch statusLine if it's in fact ours.
if (isPithStatusLine(s.statusLine)) {
  let original = null;
  try {
    const cfg = JSON.parse(fs.readFileSync(pithCfgPath, 'utf8'));
    if (cfg && cfg.original_statusline && cfg.original_statusline.command) {
      original = cfg.original_statusline;
    }
  } catch (_) {}
  if (original) {
    s.statusLine = original;
    console.log('  ✓ original statusline restored');
  } else {
    delete s.statusLine;
  }
}

fs.writeFileSync(p, JSON.stringify(s, null, 2));
console.log('  ✓ settings.json cleaned');
NODESCRIPT
fi

echo ""
echo "Pith uninstalled. Token state preserved at ~/.pith/state.json"
echo "To remove all data: rm -rf ~/.pith"
echo ""
