---
allowed-tools: Bash
description: Set a hard token ceiling per response (e.g. /budget 100) or clear it (/budget off)
---
Run the Pith budget hook and apply the token ceiling.

**Argument:** $ARGUMENTS

Step 1 — execute the hook. The argument is forwarded literally via a
single-quoted heredoc, so quotes/$()/backticks in input never reach the
shell:

```bash
node "${CLAUDE_PLUGIN_ROOT}/hooks/prompt-submit.js" --slash /budget <<'PITH_EOF_7f3a9c2e'
$ARGUMENTS
PITH_EOF_7f3a9c2e
```

Step 2 — apply the result:
- If a token limit was set: enforce it for every response from this point forward
- If the budget was cleared: resume normal response length

Step 3 — confirm in one line.
