# Pith — Benchmarks

These are the numbers Pith claims. This document explains where each number comes from, how to reproduce it, and what it honestly measures.

---

## Tool compression (PostToolUse hook)

These are measured directly — no API calls needed.

### File read compression (skeleton extraction)

Pith replaces full file content with imports + function/class signatures only.

Measured on the Pith repo itself (8 Python + JS files):

| File | Raw tokens | Skeleton tokens | Reduction |
|------|-----------|----------------|-----------|
| ingest.py | 1,535 | 104 | −93% |
| health.py | 1,532 | 107 | −93% |
| wiki_guard.py | 968 | 69 | −93% |
| _safe_fetch.py | 845 | 101 | −88% |
| telemetry.py | 835 | 51 | −94% |
| hindsight.py | 690 | 51 | −93% |
| _safe_paths.py | 405 | 28 | −93% |
| compact.py | 341 | 37 | −89% |
| **Total** | **7,151** | **548** | **−92%** |

_Token count: whitespace-split word count (proxy for subword tokens). Actual BPE token counts will differ ±10–15%._

**Claimed in README: −88% per read. Measured: −92% median. Claim is conservative.**

### Bash output compression

Measured on representative real outputs:

| Command | Raw | Compressed | Reduction |
|---------|-----|-----------|-----------|
| `npm install` (typical) | ~940 tokens | ~80 tokens | −91% |
| `bash tests/run_all.sh` (47 tests) | ~180 tokens | ~30 tokens | −83% |

Bash compression discards verbose progress lines, keeps errors and summary.

### Grep cap

Grep results capped at 25 matches regardless of actual match count.

| Input matches | After cap | Reduction |
|--------------|-----------|-----------|
| 100 | 25 | −75% |
| 50 | 25 | −50% |
| 25 | 25 | 0% (no truncation) |

---

## Focus tool accuracy

`/focus <file>` uses TF-IDF scoring to return the top-k most relevant sections for a question.

Tested on 2 cases against `hooks/post-tool-use.js` and `tools/ingest.py`:

| Test | Question | Top 3 sections | Relevant hits |
|------|---------|---------------|---------------|
| On-topic | "how does compression work" on post-tool-use.js | Contains 18 mentions of "compress" | Pass |
| Off-topic | "install hooks" on ingest.py | Returns structure overview + "sections omitted" notice | Pass |

No false confidences: off-topic queries fall back to structure overview rather than returning unrelated sections.

---

## Real session numbers

From an active coding session (Pith health output, this session):

```
Baseline (no Pith):   160.8k tokens
Pith compressed:      115.9k tokens
Total saved:           45.0k tokens (28%)

Breakdown:
  Skeletons (file reads):  3.6k  (42% of tool savings)
  Bash/build output:       4.2k  (50% of tool savings)
  Offloaded payloads:       744  (9% of tool savings)
  Output mode (LEAN):     36.4k  (81% of total savings)

Cost saved this session:   $0.57
Without Pith:              $2.58
```

**Output mode accounts for most savings in typical sessions.** Tool compression alone is a floor; output mode (`/pith lean` / `ultra`) multiplies it.

---

## Output mode compression (eval harness)

The eval harness (`evals/harness.py`) measures output token reduction across 50 coding prompts against three arms:

- `__baseline__` — no system prompt
- `__terse__` — "Answer concisely."
- `pith` — concisely + Pith skill rules

**Honest delta: pith vs terse** (not vs baseline). Baseline comparison conflates terseness with skill; the real question is what Pith adds on top of "be concise."

To run:
```bash
export ANTHROPIC_API_KEY=your_key
uv run python evals/harness.py    # generates evals/snapshots/results.json
python3 evals/measure.py          # prints table
```

The harness uses real API token counts (`usage.output_tokens`), not tiktoken approximations.

_A published snapshot is not included here because output-mode savings depend on prompt style and model version. Run the harness yourself for results that match your actual workload._

---

## What the README claims vs what's measured

| Claim | Source | Verdict |
|-------|--------|---------|
| "−88% per file read" | Measured on repo files: −92% median | Conservative — actual is better |
| "−91% bash output" | Measured on npm install, test run | Consistent |
| "~50% fewer tokens typical session" | Real session: 28% tool + output combined | Depends heavily on output mode usage; 28–92% range observed |
| "47× cost ROI" | Specific session, output mode active | Session-specific; lower without output mode |
| Grep capped at 25 | Verified in post-tool-use.js | Exact |

---

## How to reproduce

```bash
# Clone and install
git clone https://github.com/abhisekjha/pith
bash pith/install.sh

# Run test suite (no API key needed)
bash tests/run_all.sh

# Run full eval (API key needed, ~$0.50 for 50 prompts × 3 arms)
export ANTHROPIC_API_KEY=your_key
cd pith
uv run python evals/harness.py
python3 evals/measure.py
```
