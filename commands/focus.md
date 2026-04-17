---
allowed-tools: Bash
description: Load only the sections of a file relevant to the current question, reducing context noise
---
Load only the sections of a file relevant to the current question.

**File:** $ARGUMENTS

The file path is passed via stdin in a single-quoted heredoc and read by
focus.py from argv[1], so shell metacharacters in the path are treated as
literal bytes:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/tools/focus.py" --stdin-path <<'PITH_EOF_7f3a9c2e'
$ARGUMENTS
PITH_EOF_7f3a9c2e
```

Show the relevant sections to the user. If no sections match, show the file structure overview.
