# Pith

[![GitHub Stars](https://img.shields.io/github/stars/abhisekjha/pith?style=social)](https://github.com/abhisekjha/pith/stargazers)

**~50% fewer tokens in a typical Claude Code session. Zero config. Install once.**

```bash
bash <(curl -s https://raw.githubusercontent.com/abhisekjha/pith/main/install.sh)
```

---

## How it works

Pith installs four hooks into Claude Code's lifecycle. Every session, every project, automatically.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Claude Code Session                         │
│                                                                 │
│  [SessionStart] ──► Inject rules, restore mode, cache-lock      │
│                                                                 │
│  User prompt                                                    │
│       │                                                         │
│  [UserPromptSubmit] ──► Parse /pith commands, inject context    │
│       │                                                         │
│  Claude thinks + calls tools                                    │
│       │                                                         │
│  [PostToolUse] ──► Compress tool output                         │
│    • File reads   → skeleton (imports + signatures only)        │
│    • Bash output  → errors + 3-line summary                     │
│    • Grep results → capped at 25 matches                        │
│    • Large output → offloaded to ~/.pith/tmp/, 3-line pointer   │
│       │                                                         │
│  Claude responds                                                │
│       │                                                         │
│  [Stop] ──► Record token usage, accumulate lifetime stats       │
└─────────────────────────────────────────────────────────────────┘
```

The PostToolUse hook is where most savings happen — file reads alone account for 30–50% of context in a typical coding session.

---

## What gets compressed

| Layer | What happens | Typical saving |
|-------|-------------|----------------|
| File reads | skeleton only — imports, signatures, types | −88% per read |
| Bash/build output | errors + summary, discard verbose logs | −91% per run |
| Grep results | capped at 25 matches | prevents runaway searches |
| Large payloads | offloaded to `~/.pith/tmp/`, pointer left in context | prevents bloat |
| Symbol extraction | exact function/class lines via tree-sitter | ~95% vs full file |
| Output compression | lean/ultra modes, auto-escalation as context fills | 25–42% of output |
| Auto-compact | history summarized at 70% fill | unlimited sessions |
| Cache-Lock | session rules hashed — skipped if unchanged | ~300 tok/session |

In a 30-turn coding session: tool compression + auto-compact alone cuts spend by ~40–55%.

---

## Install

```bash
bash <(curl -s https://raw.githubusercontent.com/abhisekjha/pith/main/install.sh)
```

Or from source:
```bash
git clone https://github.com/abhisekjha/pith
bash pith/install.sh
```

Or from inside Claude Code:
```
/pith install
```

One command. Hooks install globally into `~/.claude/hooks/`. Every session from that point on.

---

## Key commands

**Token status**
```
/pith status    → ASCII token flow chart — baseline vs compressed vs output + plain-English savings insight
/pith report    → interactive HTML dashboard (~/.pith/report.html) — auto-refreshes every 30s
```

![/pith status — token flow chart, compression ratio, cost breakdown](assets/status-screenshot-v1.1.png)

**Output compression** (on demand or automatic)
```
/pith           → lean mode (drop filler, short synonyms)
/pith ultra     → maximum (abbreviate, arrows, tables)
/pith precise   → tight but full sentences
/pith off       → disable
```

Auto-escalation ratchets compression as context fills:
```
50% fill → LEAN activated
70% fill → ULTRA activated
85% fill → dynamic token ceiling
```

**Code navigation**
```
/pith symbol src/auth.ts handleLogin   → exact 30–50 lines (~95% vs full file)
/pith symbol --list src/auth.ts        → all symbols with line numbers
/pith focus src/services/auth.ts       → load only sections relevant to question
```

**Project wiki**
```
/pith ingest <file>                    → extract entities, claims, contradictions
/pith ingest --url <url>               → fetch URL and ingest
/pith compile                          → re-synthesize all sources into wiki pages
/pith wiki "how does auth work?"       → search with citations
/pith lint                             → contradictions, gaps, missing pages
```

**Structured formats**
```
/pith debug     problem / cause / fix / verify
/pith review    L42: BUG token expiry. Fix: token.exp * 1000
/pith arch      options table + decision + risks
/pith plan      numbered steps + risks + done-when
/pith commit    feat(auth): add token refresh on 401
```

**Update**
```
/pith update    → pull latest Pith version and re-sync all hooks
```

Already installed? `/pith update` runs `git pull` on `~/.local/share/pith` and re-runs `install.sh` — hooks, slash commands, and settings all re-sync in one step.

→ Full command reference: [COMMANDS.md](COMMANDS.md)

---

## jCodeMunch — structure-aware code ingestion

When you ingest a source code file (`.py`, `.ts`, `.js`, `.go`, `.rs`, `.java`, etc.), Pith takes a different path than it does for docs or notes.

**Normal ingest** (markdown, text): feeds raw content → extracts entities, concepts, decisions.

**jCodeMunch** (code files): feeds skeleton → extracts architecture.

```
/pith ingest src/services/auth.ts
→ [jCodeMunch] Code file detected (ts) — using structure-aware analysis...
```

### What it does

1. Runs `symbols.py` on the file — extracts imports, function/class signatures, types. No body.
2. Passes the skeleton (not raw source) + first 120 lines to Claude for analysis.
3. Creates **Module** and **Class** wiki pages, not generic Concept pages.

```
wiki/entities/
  AuthService.md      ← exports, dependencies, key methods
  TokenValidator.md   ← class: methods, relationships, contracts
```

### Why it matters

| Regular ingest | jCodeMunch |
|---------------|------------|
| Feeds full file source | Feeds AST skeleton only |
| Creates generic Concept pages | Creates Module/Class pages |
| Tracks claims and facts | Tracks exports, methods, deps |
| ~4000 token budget | ~500 token skeleton → cheaper |

The wiki stays current with your architecture, not just your docs.
Cross-links (`[[AuthService]]` → `[[TokenValidator]]`) appear automatically in `/pith-graph`.

### What you can do with it

```bash
# Map a whole service layer
/pith ingest src/services/auth.ts
/pith ingest src/services/user.ts
/pith ingest src/services/payments.ts

# Then query across them
/pith wiki "which services depend on the token validator?"
/pith-graph   → visual dep map in browser
```

**Brownfield bootstrap:** run ingest on your key files once, get a navigable wiki of module responsibilities, exports, and cross-module deps — in one session.

---

## Multi-model pricing

Pith detects the model from the conversation transcript and applies correct per-token rates automatically. No config needed.

| Model | Input ($/MTok) | Output ($/MTok) |
|-------|---------------|----------------|
| Claude Opus 4.7 / 4.6 / 4.5 | $5.00 | $25.00 |
| Claude Opus 4.1 / 4 | $15.00 | $75.00 |
| Claude Sonnet 4.6 / 4.5 / 4 | $3.00 | $15.00 |
| Claude Sonnet 3.7 | $3.00 | $15.00 |
| Claude Haiku 4.5 | $1.00 | $5.00 |
| Claude Haiku 3.5 | $0.80 | $4.00 |
| Claude Opus 3 | $15.00 | $75.00 |
| Claude Haiku 3 | $0.25 | $1.25 |

Falls back to Sonnet 4.6 rates if model can't be determined.

---

## Honest numbers

These are estimates against real usage — not against "no system prompt."

```
Before Pith:  Read large-service.ts → 1,800 tokens
After Pith:   Read large-service.ts →   210 tokens   (−88%)

Before Pith:  npm install output    →   940 tokens
After Pith:   errors + summary      →    80 tokens   (−91%)
```

Session report from a real coding session:
- 105.6k input tokens (would be ~112.6k without Pith)
- 65.8k output tokens
- $1.30 actual spend vs $2.39 without
- Compression ratio: 1.1:1 tool compression + 88.8k output mode savings

Token savings come from two independent buckets:
- **Tool compression** (`PostToolUse`) — trims file reads, bash, grep
- **Output mode** (`/pith lean/ultra`) — trims Claude's responses

Both are tracked separately; `total_saved = tool_saved + output_saved`.

---

## Project wiki

Pith maintains a persistent knowledge base alongside your code. Three workflows:

**Greenfield** — three questions on first run, wiki grows as you build.
**Brownfield** — one-session bootstrap reads the codebase and writes initial pages.
**Ongoing** — after decisions, bugs, and architecture discussions, one-line offer to save.

```
my-project/
  wiki/
    index.md        ← catalog of all pages
    log.md          ← session history
    entities/       ← tools, services, components
    concepts/       ← patterns, methods, ideas
    decisions/      ← architecture decision records
  raw/sources/      ← drop documents here, /pith ingest them
```

---

## Evals

```bash
uv run python evals/harness.py    # generate snapshot (needs ANTHROPIC_API_KEY)
python3 evals/measure.py          # analyze snapshot
```

Three arms: **baseline / terse / pith**. Honest delta is pith vs terse — how much the skill adds on top of "Answer concisely."

---

## Uninstall

```bash
/pith uninstall
```

Removes hooks from `~/.claude/settings.json`. History preserved at `~/.pith/`.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=abhisekjha/pith&type=Date)](https://star-history.com/#abhisekjha/pith&Date)

---

## License

MIT
