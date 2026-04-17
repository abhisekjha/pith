---
allowed-tools: Bash
description: Control Pith token compression modes and access structured output formats, wiki, and status
---
Run the Pith hook for this subcommand and apply the result.

**Subcommand:** $ARGUMENTS

Step 1 — execute the hook. The arguments are passed via a single-quoted
heredoc so any shell metacharacters the user typed ('"$`;|&) are forwarded
as literal bytes and cannot be evaluated by the shell:

```bash
node "${CLAUDE_PLUGIN_ROOT}/hooks/prompt-submit.js" --slash /pith <<'PITH_EOF_7f3a9c2e'
$ARGUMENTS
PITH_EOF_7f3a9c2e
```

Step 2 — act on the output:
- If the output contains compression rules (LEAN / ULTRA / PRECISE): apply them immediately and confirm the mode is active
- If the output contains a token status panel: display it to the user exactly
- If the output contains wiki results or tool output: display it
- If the output is empty (e.g. `/pith debug`): confirm the structured format is active and show a one-line example of it

Step 3 — confirm to the user in one line what changed or was shown.
