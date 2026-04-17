# Pith — Command Reference

## Compression

| Command | What it does |
|---------|-------------|
| `/pith` or `/pith lean` | Lean compression — drop filler, short synonyms, fragments OK |
| `/pith precise` | Professional and tight — no filler, full sentences |
| `/pith ultra` | Maximum — arrows, tables, abbreviate freely |
| `/pith off` | Disable output compression |
| `/pith on` | Restore last saved mode |
| `/pith escalate on\|off` | Enable/disable auto-escalation |

## Session & Analytics

| Command | What it does |
|---------|-------------|
| `/pith status` | ASCII token flow chart + health report with plain-English savings insight |
| `/pith report` | Interactive HTML dashboard (opens in browser, auto-refreshes every 30s) |
| `/pith hindsight` | Identify stale tool results, recommend /compact |
| `/pith escalate` | Show auto-escalation status and thresholds |
| `/pith budget <N>` | Hard token ceiling per response |
| `/pith budget off` | Clear token ceiling |
| `/pith recall` | Restore last session's mode/wiki/budget |
| `/pith configure` | Interactive config wizard |
| `/pith reset-cache` | Force full session-start injection next session |
| `/pith tour` | 7-step interactive guided tour |

## Structured Formats

| Command | Output shape |
|---------|-------------|
| `/pith debug` | Problem / Cause / Fix / Verify |
| `/pith review` | One line per issue: `L42: BUG ...` |
| `/pith arch` | Options table / Choice / Risks / Next |
| `/pith plan` | Goal / Numbered steps / Risks / Done-when |
| `/pith commit` | Conventional Commits format |

## Code Navigation

| Command | What it does |
|---------|-------------|
| `/pith symbol <file> <name>` | Extract exact source lines (~95% fewer tokens vs full file) |
| `/pith symbol --list <file>` | List all top-level symbols with line numbers |
| `/pith focus <file>` | Load only sections relevant to current question (restricted to project directory) |

## Wiki

| Command | What it does |
|---------|-------------|
| `/pith wiki` | Toggle wiki mode on/off |
| `/pith wiki "<question>"` | Search wiki, synthesize answer with citations |
| `/pith ingest <file>` | Extract entities/claims/contradictions, update wiki |
| `/pith ingest --url <url>` | Fetch URL, save to `raw/sources/`, ingest |
| `/pith compile` | Batch re-synthesis from all sources (Karpathy compile pass) |
| `/pith compile --topic <topic>` | Recompile pages matching topic |
| `/pith compile --dry-run` | Show plan without writing |
| `/pith lint` | Full semantic lint: contradictions, gaps, missing pages, imputable facts |
| `/pith lint --fix` | Lint + auto-create stubs for missing entities |
| `/pith lint --quick` | Structural checks only, no LLM |
| `/pith-graph` | Force-directed wiki knowledge graph |

## Integrations

| Command | What it does |
|---------|-------------|
| `/pith grepai` | Show GrepAI semantic search status |
| `/pith grepai skip` | Dismiss install nudge |
| `/pith grepai enable` | Re-enable nudge |

## Install / Manage

| Command | What it does |
|---------|-------------|
| `/pith install` | Install Pith into Claude Code |
| `/pith uninstall` | Remove Pith cleanly |
| `/pith update` | Pull latest Pith version, re-sync all hooks and settings |
| `/pith setup` | Re-run first-session onboarding |
| `/pith optimize-cache` | Restructure CLAUDE.md for prompt caching |
| `/pith help` | This reference, inline |
