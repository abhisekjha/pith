#!/usr/bin/env bash
set -e

# ── Self-clone when piped via curl ────────────────────────────────────────────
# bash <(curl -s ...) sets BASH_SOURCE[0] to /dev/fd/<n> — there are no
# hook files there.  Detect this and clone to a stable location, then re-exec.
_src="${BASH_SOURCE[0]}"
if [[ "$_src" == /dev/fd/* ]] || [[ "$_src" == /proc/*/fd/* ]] || [[ "$_src" == "" ]]; then
  INSTALL_SRC="${HOME}/.local/share/pith"
  echo ""
  echo "Detected curl-pipe install — cloning repo to ${INSTALL_SRC}..."
  mkdir -p "$(dirname "${INSTALL_SRC}")"
  if [ -d "${INSTALL_SRC}/.git" ]; then
    git -C "${INSTALL_SRC}" pull --quiet --ff-only
  else
    git clone --depth=1 --quiet https://github.com/abhisekjha/pith.git "${INSTALL_SRC}"
  fi
  exec bash "${INSTALL_SRC}/install.sh"
fi

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
HOOKS_DIR="${CLAUDE_DIR}/hooks/pith"
SETTINGS="${CLAUDE_DIR}/settings.json"

echo ""
echo "Installing Pith..."
echo ""

# Create dirs
mkdir -p "${HOOKS_DIR}"
mkdir -p "${HOME}/.pith"

# Copy hooks
for f in session-start.js post-tool-use.js prompt-submit.js stop.js config.js statusline.sh; do
  cp "${PLUGIN_ROOT}/hooks/${f}" "${HOOKS_DIR}/${f}"
  echo "  ✓ hooks/${f}"
done
chmod +x "${HOOKS_DIR}/statusline.sh"

# Create symlink to skills and tools so hooks can resolve paths
ln -sfn "${PLUGIN_ROOT}/skills" "${HOOKS_DIR}/../pith-skills" 2>/dev/null || true
ln -sfn "${PLUGIN_ROOT}/tools"  "${HOOKS_DIR}/../pith-tools"  2>/dev/null || true

# Patch settings.json
node - "${SETTINGS}" "${HOOKS_DIR}" <<'NODESCRIPT'
const fs = require('fs');
const path = require('path');

const settingsPath = process.argv[2];
const hooksDir     = process.argv[3];

let settings = {};
try { settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8')); } catch (e) {}

const hook = (script, timeout, msg) => ({
  type: 'command',
  command: `node "${hooksDir}/${script}"`,
  ...(timeout  ? { timeout }        : {}),
  ...(msg      ? { statusMessage: msg } : {}),
});

const hookEntry = (event, script, timeout, msg) => ({
  matcher: '',
  hooks: [hook(script, timeout, msg)],
});

settings.hooks = settings.hooks || {};

const add = (event, script, timeout, msg) => {
  const list = settings.hooks[event] = settings.hooks[event] || [];
  const exists = list.some(h => h.hooks && h.hooks[0] && h.hooks[0].command && h.hooks[0].command.includes('pith'));
  if (!exists) list.push(hookEntry(event, script, timeout, msg));
};

add('SessionStart',      'session-start.js',  10, 'Pith: initializing...');
add('UserPromptSubmit',  'prompt-submit.js',   5, null);
add('PostToolUse',       'post-tool-use.js',  10, 'Pith: compressing...');
add('Stop',              'stop.js',            5, null);

settings.statusLine = {
  type: 'command',
  command: `bash "${hooksDir}/statusline.sh"`,
};

fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
console.log('  ✓ settings.json patched');
NODESCRIPT

# Install slash commands into ~/.claude/commands/
COMMANDS_DIR="${CLAUDE_DIR}/commands"
mkdir -p "${COMMANDS_DIR}"

node - "${COMMANDS_DIR}" "${HOOKS_DIR}" "${PLUGIN_ROOT}" <<'CMDSCRIPT'
const fs   = require('fs');
const path = require('path');
const cmds = process.argv[2];
const hooks = process.argv[3];
const root  = process.argv[4];

// NOTE: $ARGUMENTS is Claude Code's literal template substitution — it is NOT
// shell-escaped. The only injection-safe way to forward user args into bash is
// to place them inside a single-quoted heredoc whose delimiter cannot appear
// in normal user input. The helper then reads stdin instead of argv.
const HEREDOC_TAG = 'PITH_EOF_7f3a9c2e';

const pithMd = `---
allowed-tools: Bash
---
Run the Pith hook for this subcommand and apply the result.

**Subcommand:** $ARGUMENTS

Step 1 — execute the hook. Arguments flow via a single-quoted heredoc so
shell metacharacters in user input are never evaluated:
\`\`\`bash
node "${hooks}/prompt-submit.js" --slash /pith <<'${HEREDOC_TAG}'
$ARGUMENTS
${HEREDOC_TAG}
\`\`\`

Step 2 — act on the output:
- If the output contains compression rules (LEAN / ULTRA / PRECISE): apply them immediately and confirm the mode is active
- If the output contains a token status panel: display it to the user exactly
- If the output contains wiki results or tool output: display it
- If the output is empty (e.g. \`/pith debug\`): confirm the structured format is active and show a one-line example of it

Step 3 — confirm to the user in one line what changed or was shown.
`;

const budgetMd = `---
allowed-tools: Bash
---
Run the Pith budget hook and apply the token ceiling.

**Argument:** $ARGUMENTS

Step 1 — execute the hook (arguments passed via heredoc, never via shell args):
\`\`\`bash
node "${hooks}/prompt-submit.js" --slash /budget <<'${HEREDOC_TAG}'
$ARGUMENTS
${HEREDOC_TAG}
\`\`\`

Step 2 — apply the result:
- If a token limit was set: enforce it for every response from this point forward
- If the budget was cleared: resume normal response length

Step 3 — confirm in one line.
`;

const focusMd = `---
allowed-tools: Bash
---
Load only the sections of a file relevant to the current question.

**File:** $ARGUMENTS

The file path is delivered to focus.py via a heredoc, so metacharacters in
the path cannot trigger shell evaluation:
\`\`\`bash
python3 "${root}/tools/focus.py" --stdin-path <<'${HEREDOC_TAG}'
$ARGUMENTS
${HEREDOC_TAG}
\`\`\`

Show the relevant sections to the user. If no sections match, show the file structure overview.
`;

const graphMd = `---
allowed-tools: Bash
---
Run the Pith wiki graph generator for the current project.

\`\`\`bash
python3 "${root}/tools/graph_generator.py"
\`\`\`

The script will:
1. Scan \`./wiki/\` for \`.md\` files and extract \`[[wikilinks]]\`
2. Write \`wiki-graph.html\` to the current project root
3. Automatically open it in the default browser

If \`wiki/\` does not exist or has no \`.md\` files, tell the user to build the wiki first with \`/pith wiki\`.
`;

fs.writeFileSync(path.join(cmds, 'pith.md'),       pithMd);
fs.writeFileSync(path.join(cmds, 'budget.md'),     budgetMd);
fs.writeFileSync(path.join(cmds, 'focus.md'),      focusMd);
fs.writeFileSync(path.join(cmds, 'pith-graph.md'), graphMd);
console.log('  ✓ slash commands registered (/pith, /budget, /focus, /pith-graph)');
CMDSCRIPT

# Write CLAUDE_PLUGIN_ROOT to config so hooks can find skills/tools
mkdir -p "${HOME}/.config/pith"
node -e "
const fs = require('fs');
const p = '${HOME}/.config/pith/config.json';
let c = {};
try { c = JSON.parse(fs.readFileSync(p,'utf8')); } catch(e) {}
c.plugin_root = '${PLUGIN_ROOT}';
fs.writeFileSync(p, JSON.stringify(c, null, 2));
"
echo "  ✓ plugin root recorded"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Pith installed successfully."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Automatic features (active now, zero config):"
echo "  • Tool output compression — large file reads, bash, grep trimmed automatically"
echo "  • Token meter in statusline — [PITH 12k/200k]"
echo "  • Auto-compact at 70% — sessions never hit context limits"
echo "  • First-run wiki setup — offered on your first session in a new project"
echo ""
echo "On-demand commands:"
echo "  /pith                → lean output compression"
echo "  /pith precise|ultra  → adjust compression level"
echo "  /pith debug          → structured debug format"
echo "  /pith review         → structured code review"
echo "  /pith arch           → architecture decision table"
echo "  /pith plan           → task breakdown format"
echo "  /pith commit         → conventional commit message"
echo "  /pith wiki           → toggle wiki mode"
echo "  /pith status         → token usage this session"
echo "  /budget 150          → hard token ceiling"
echo "  /focus <file>        → extract relevant sections"
echo ""
echo "Start Claude Code in any project to begin."
echo ""
