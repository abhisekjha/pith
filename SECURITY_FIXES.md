# Pith — Security Fix Plan

Scope: concrete plan to fix the issues found in the security review of
`abhisekjha/pith` @ `9569792`. Seven findings, prioritised by severity and
ordered so that shared helpers (the argv builder, the safe-path helper) land
first and later patches can reuse them.

Threat model recap: these hooks run on **every** Claude Code session, every
prompt, and every tool call with the user's local privileges. The attack
surface includes (a) the user's own prompt text, (b) tool output that Claude
receives — which can be attacker-controlled via Read/Bash/WebFetch/ingest, and
(c) supply-chain: the hooks file on disk is auto-installed from `main`.

---

## H-1 — Path traversal via LLM-controlled paths (ingest.py, compile.py)

**Files:** `tools/ingest.py:328,373`, `tools/compile.py:256,275,290,305`
**Risk:** The page path is taken from an LLM JSON response whose input came
from untrusted source content (file or URL). `Path(cwd) / '/abs/path'` returns
the right-hand side, so a prompt-injected reply can write anywhere the user
can write — including `~/.claude/hooks/pith/post-tool-use.js`, turning the
bug into persistent RCE on every subsequent Claude Code session.

**Fix**

1. Add a helper in a new `tools/_safe_paths.py`:
   ```python
   from pathlib import Path

   def safe_wiki_path(cwd: Path, rel: str) -> Path:
       """Resolve `rel` under cwd/wiki/ and refuse anything outside it."""
       if not rel or not isinstance(rel, str):
           raise ValueError("page path missing")
       wiki_root = (cwd / "wiki").resolve()
       # reject absolute paths and drive letters outright
       candidate = (cwd / rel).resolve()
       try:
           candidate.relative_to(wiki_root)
       except ValueError:
           raise ValueError(f"path escapes wiki/: {rel!r}")
       # also reject symlinks that resolve elsewhere
       if candidate.is_symlink():
           raise ValueError(f"refusing symlink page path: {rel!r}")
       return candidate
   ```
2. Replace every `cwd / spec['path']` / `cwd / page_spec['path']` /
   `cwd / existing_path` with `safe_wiki_path(cwd, ...)`. Call sites:
   `ingest.py:328`, `compile.py:256`, `compile.py:290`.
3. Do the same for `update_index`'s `idx_path` and `append_log`'s `log_path`
   — they're already literal, but the helper makes the invariant explicit.
4. When `safe_wiki_path` raises, log and skip that page — do **not** abort
   the whole ingest run (one poisoned entry shouldn't prevent later legit
   pages from being written).
5. Add `tests/test_safe_paths.py` covering: absolute paths, `..` traversal,
   symlinks, empty/None, and valid `wiki/entities/foo.md`.

**Done when:** an ingest run against a source whose LLM reply names
`/tmp/pwn.txt`, `../../.claude/hooks/pith/post-tool-use.js`, and
`wiki/entities/legit.md` writes only the third and logs the first two as
refused.

---

## H-2 — Shell injection in `hooks/prompt-submit.js` `runTool()`

**File:** `hooks/prompt-submit.js:530-540`
**Risk:** `cmd` is a template string passed to `execSync`. Only `"` is
escaped, so `$(…)`, backticks, and `$VAR` expand inside the double-quoted
argument. Reachable via `/pith ingest <path>`, `/pith focus <path>`,
`/pith symbol <f> <name>`, `/pith wiki "<q>"`, `/focus <path>` (which also
forwards the full user prompt as `--question`), and the hindsight nudge which
runs on every non-`/pith` prompt above 60 % context.

**Fix**

1. Replace `execSync` + template with `execFileSync` + argv:
   ```js
   const { execFileSync } = require('child_process');

   function runTool(script, args, root, extraArgs) {
     const toolPath = path.join(root, 'tools', script);
     if (!fs.existsSync(toolPath)) return `[PITH: tool ${script} not found]`;
     const argv = [toolPath, ...args.map(String)];
     if (extraArgs) argv.push(...String(extraArgs).split(/\s+/).filter(Boolean));
     try {
       return execFileSync('python3', argv, {
         timeout: 30000,
         encoding: 'utf8',
         cwd: process.env.CLAUDE_CWD || process.cwd(),
         stdio: ['ignore', 'pipe', 'pipe'],
         maxBuffer: 8 * 1024 * 1024,
       }).trim();
     } catch (e) {
       return `[PITH: ${script} failed — ${(e.stderr?.toString() || e.message || '').slice(0, 200)}]`;
     }
   }
   ```
2. Audit every `extraArgs` caller. Today only `tour.py` uses it with a
   hard-coded string; that split is safe, but callers must never pass
   user data through `extraArgs` — document it or refactor to always use
   the `args` array.
3. Drop the `escaped` helper entirely.
4. Add a regression test: call `runTool('focus.py', ['$(touch /tmp/pith_pwn)'], root)`
   and assert `/tmp/pith_pwn` does **not** exist afterwards.

**Done when:** no code path in any hook builds a shell string with user
input. `grep -rn "execSync\|shell: true" hooks/` returns nothing.

---

## H-3 — Shell injection in slash-command definitions

**Files:** `commands/pith.md:11`, `commands/budget.md:11`,
`commands/focus.md:10`, and the identical templates inside `install.sh`
(the `pithMd`, `budgetMd`, `focusMd` heredocs).

**Risk:** `echo '{"prompt":"/pith $ARGUMENTS"}' | node ...` — a single `'` in
user args terminates the shell literal and what follows runs as a shell
command. `$ARGUMENTS` is also JSON-unsafe. These commands declare
`allowed-tools: Bash`, so Claude runs the bash block without prompting.

**Fix**

1. Replace each command body with a form that never interpolates args into
   shell-quoted JSON. Minimal safe pattern:
   ```bash
   printf '%s' "$ARGUMENTS" | node "${CLAUDE_PLUGIN_ROOT}/hooks/prompt-submit.js" --stdin-prompt /pith
   ```
   …**and** teach `prompt-submit.js` to accept `--stdin-prompt <prefix>`:
   read stdin, prepend `<prefix> ` to form the prompt, build the `data`
   object in JS, and proceed. That removes the "embed args into JSON in
   bash" problem entirely.
2. If a bigger refactor is too much, the stop-gap is:
   ```bash
   node "${CLAUDE_PLUGIN_ROOT}/hooks/prompt-submit.js" --cli /pith -- "$@"
   ```
   passing argv positionally. No JSON, no shell quoting needed.
3. Update `install.sh` (the Node heredoc that writes the three `.md`
   files) to the chosen pattern so fresh installs don't regress.
4. Add an install-time check: after writing the commands, `grep` them for
   `$ARGUMENTS` inside single-quoted JSON and fail the install if found.

**Done when:** typing `/pith x'; touch /tmp/pith_pwn; echo '` in Claude Code
does not create `/tmp/pith_pwn`.

---

## M-4 — SSRF in `tools/ingest.py` `fetch_url()`

**File:** `tools/ingest.py:411-449`
**Risk:** `urllib.request.urlopen` accepts `file://`, `ftp://`, `gopher://`,
and arbitrary hosts including loopback, link-local (`169.254.169.254` AWS
IMDS), and RFC1918 ranges. The fetched body feeds into the LLM prompt and
(via H-1, which we'll fix first) into `write_text` calls.

**Fix**

1. Add `tools/_safe_fetch.py`:
   ```python
   import ipaddress, socket
   from urllib.parse import urlparse
   import urllib.request

   _ALLOWED_SCHEMES = {"http", "https"}
   _MAX_BYTES = 5 * 1024 * 1024       # 5 MiB
   _TIMEOUT   = 20

   def _is_public(host: str) -> bool:
       try:
           infos = socket.getaddrinfo(host, None)
       except socket.gaierror:
           return False
       for info in infos:
           ip = ipaddress.ip_address(info[4][0])
           if (ip.is_private or ip.is_loopback or ip.is_link_local
               or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
               return False
       return True

   def safe_fetch(url: str) -> tuple[bytes, str]:
       p = urlparse(url)
       if p.scheme not in _ALLOWED_SCHEMES:
           raise ValueError(f"scheme not allowed: {p.scheme}")
       if not p.hostname:
           raise ValueError("no hostname")
       if not _is_public(p.hostname):
           raise ValueError(f"refusing non-public host: {p.hostname}")
       req = urllib.request.Request(
           url, headers={"User-Agent": "pith-ingest/1.1"}
       )
       with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
           if resp.status >= 400:
               raise ValueError(f"HTTP {resp.status}")
           body = resp.read(_MAX_BYTES + 1)
           if len(body) > _MAX_BYTES:
               raise ValueError("response too large")
           return body, resp.headers.get("Content-Type", "")
   ```
2. `fetch_url` uses `safe_fetch(url)` instead of `urlopen` directly.
3. Resolve the host **once** and connect to that resolved IP to prevent
   DNS-rebinding (optional, low priority — document as a residual risk if
   we don't do it).
4. Opt-in override: `PITH_INGEST_ALLOW_PRIVATE=1` env var re-enables RFC1918
   for power users who intentionally ingest from an internal wiki. Off by
   default.
5. Test matrix: `file:///etc/passwd`, `http://127.0.0.1`, `http://169.254.169.254/latest/meta-data/`,
   `http://10.0.0.1`, and one valid public URL (mocked).

**Done when:** `/pith ingest --url file:///etc/passwd` and
`--url http://169.254.169.254/…` both fail with "scheme not allowed" /
"refusing non-public host".

---

## M-5 — `curl | bash` installer pulls unpinned `main`

**File:** `install.sh:14-18`, and the README install line that pipes it.

**Risk:** No commit pin, no signature, no hash verification. Upstream
compromise (or MITM on the HTTPS fetch, though less likely) ships arbitrary
code to anyone who re-runs the installer or whose `git pull --ff-only` fires
on update.

**Fix (staged)**

Stage 1 — minimum viable:
1. Pin to a tag in `install.sh` self-clone:
   `git clone --branch v${PITH_VERSION} --depth=1 …`  where
   `PITH_VERSION` is baked in at release time.
2. Verify the commit SHA matches an embedded allow-list after clone, fail
   otherwise.

Stage 2 — release hygiene:
3. Tag releases, sign them (`git tag -s`), and publish the public key in
   the README + in `install.sh` as a gpg verification step (optional, gated
   behind `PITH_VERIFY_GPG=1`).
4. Replace the README `bash <(curl …)` line with a two-step copy-paste:
   download to a file, show the SHA256 and the version, then run it. Users
   who accept the risk of one-liner installs can still do so; the default
   shown to readers is the safer form.
5. Record the resolved plugin root + version in `~/.config/pith/config.json`
   so `/pith status` can show which version is active.

Stage 3 — update path:
6. Add a `/pith update` command that pulls only signed tags, refuses
   unsigned ones, and prints a diff of hook file hashes before applying.

**Done when:** `install.sh` refuses to run against a clone whose HEAD SHA
isn't on the allow-list, and the README's primary example is the two-step
form.

---

## M-6 — Stored XSS in `tools/graph_generator.py`

**File:** `tools/graph_generator.py:578-594`

**Risk:** `html.replace("__NODES__", json.dumps(nodes))` — `json.dumps` does
not escape `</script>`. A wiki title crafted from URL-ingested content can
break out of the script tag. The output is opened via `webbrowser.open(file://…)`,
which in many browsers grants the page broad read access to neighbouring
local files.

**Fix**

1. Inline JSON into a `type="application/json"` block and parse at runtime:
   ```html
   <script id="pith-graph-data" type="application/json">__DATA__</script>
   <script>
     const data = JSON.parse(document.getElementById('pith-graph-data').textContent);
     const nodes = data.nodes, edges = data.edges;
     /* … */
   </script>
   ```
2. When serialising, escape `<` to its JSON unicode form so a literal
   `</script>` can never appear in the payload:
   ```python
   def _safe_json(obj) -> str:
       return (json.dumps(obj, ensure_ascii=False)
                   .replace("<", "\\u003c")
                   .replace(">", "\\u003e")
                   .replace("&", "\\u0026"))
   ```
3. Render node labels with D3's `.text(d => d.title)` (already the case in
   most of the file — audit to confirm no `.html(...)` usage).
4. Add a smoke test: generate a graph from a wiki page titled
   `</script><img src=x onerror=fetch('file:///etc/hosts')>` and assert the
   rendered HTML contains neither `</script><img` nor `onerror`.

**Done when:** open the generated `wiki-graph.html` in Chrome with a
poisoned title; the malicious handler does not fire and the title renders
as plain text.

---

## L-7 — Telemetry captures first lines of every tool result

**File:** `hooks/post-tool-use.js:141-157`
**Risk:** `~/.pith/telemetry.jsonl` stores `before_head`/`after_head` (first
3 lines of file reads, bash output, grep output) indefinitely. `cat .env`,
`env | grep TOKEN`, or reading `~/.aws/credentials` lands verbatim in the
log — which is a file users often share when filing issues.

**Fix**

1. Stop storing `before_head` / `after_head` by default. Gate behind
   `PITH_TELEMETRY_VERBOSE=1`. Default log keeps only lengths and ratios.
2. Redact common secret shapes before write: keys matching
   `(?i)(password|secret|token|api[_-]?key|bearer|aws_[a-z_]+)\s*[:=]\s*\S+`
   become `***REDACTED***`. Plus hex/base64 strings ≥ 32 chars.
3. Rotate the file: cap at 10 MiB, rename to `.jsonl.1`, start fresh.
4. Add a one-liner `/pith telemetry purge` command that unlinks
   `~/.pith/telemetry.jsonl*` and prints the count of freed bytes.
5. Document in README: "`~/.pith/telemetry.jsonl` is local-only; scrub
   before sharing."

**Done when:** running `cat .env` inside a session where `.env` contains
`SECRET=abc123` produces a telemetry entry without `abc123`.

---

## L-8 — `statusline.sh` interpolates `$HOME` into `python3 -c`

**File:** `hooks/statusline.sh:24-53`

**Fix**

Pass the state path as argv and read it in Python, don't interpolate:

```bash
STATE="${HOME}/.pith/state.json"
MODE=$(python3 - "$STATE" "$PROJ_KEY" <<'PY' 2>/dev/null || echo off
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get(sys.argv[2], {}).get("mode", "off"))
except Exception:
    print("off")
PY
)
```

Collapse the three repeated `python3 -c` blocks into one that returns
`mode\twiki\ttokens\tpct` in a single invocation — the status line fires on
every keypress, so reducing three Python starts to one is a real win.

**Done when:** statusline still shows the same badge, but contains no
interpolated variables inside any `python3 -c` literal.

---

## Cross-cutting hardening (not findings — recommended alongside)

- **Permissions on install:** `install.sh` should `chmod 600` state and
  telemetry files. Today they inherit umask, often 644.
- **Uninstaller correctness:** `uninstall.sh:36-38` filters hook entries
  with `JSON.stringify(entry).includes('pith')` — any unrelated hook whose
  path happens to contain "pith" will be silently deleted. Tighten to
  match the specific command prefix Pith installs.
- **Input-length caps:** every hook JSON parse should bound `raw.length`
  before `JSON.parse` (DoS belt-and-suspenders, since Claude Code already
  times out at 5 s but a pathological stdin can chew that).
- **CI:** add a GitHub Actions job that runs `npm audit --prod`,
  `ruff check tools/`, a `grep` for `execSync`/`shell=True`, and the new
  path-traversal + shell-injection + SSRF test suites on every PR.
- **SECURITY.md:** publish a vulnerability reporting policy (email +
  response SLA) so reports don't end up on the public issue tracker.

---

## Suggested rollout

| Batch | Fixes | Why group | Ship as |
|-------|-------|-----------|---------|
| 1 | H-2, H-3, L-8 | Pure shell-injection hardening, no behaviour change, low risk of regression | `v1.1.1` patch |
| 2 | H-1, M-4, M-6 | Requires the new `safe_wiki_path` / `safe_fetch` helpers and new tests | `v1.2.0` |
| 3 | M-5 (stages 1-2), L-7, cross-cutting | Release hygiene + telemetry scrub; coordinate with a docs pass | `v1.3.0` |
| 4 | M-5 stage 3 (`/pith update`) | Needs signing infra | `v1.4.0` |

Batches 1 and 2 should land within the same week — together they close
every RCE path. Batch 3 is a week later, batch 4 whenever signing infra is
in place.
