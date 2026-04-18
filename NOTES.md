# Notes to future-me about Pith

Written at v1.0.0. Read this before opening a .py file.

---

## Why I built it

My Claude Code sessions were burning 150k+ tokens on file reads and verbose output. There was no native way to compress tool results. I built Pith to fix that for myself — not to ship a product, not to build a community, not to compete with anything.

## What it does well (the 5 things I actually use)

1. **Auto tool compression** — file reads come back as skeletons, bash comes back as errors + summary. Zero config. This runs every session without me thinking about it.
2. **Output modes** — `/pith lean` when I want terse answers, `/pith ultra` when context is getting full. Cuts Claude's response length by 30–40% without losing the answer.
3. **Token status** — `/pith status` tells me what the session actually cost and what was saved. Useful for sanity-checking expensive sessions.
4. **Focus** — `/focus <file>` when I want to ask a question about a file without reading all of it. Saves me from reading 500-line files to ask about one function.
5. **Symbol extraction** — `/pith symbol` to pull exact function bodies. 30–50 lines instead of 500.

## What I explicitly decided not to pursue

- **Wiki as a product** — The ingest/compile/wiki system works, but it's not something I'd use daily. I built it because it was interesting, not because I needed it. It stays in the codebase for the people who want it.
- **Knowledge graph polish** — The graph generator is functional. Making it beautiful would be a week of D3 work with no personal payoff.
- **Marketing / community building** — No Discord, no newsletter, no launch strategy. The README is the surface. Stars are nice, not the point.
- **Pith-as-a-service or SaaS** — This is a local tool. It runs hooks. It saves tokens. That's the whole thing.
- **Supporting other AI tools** — Built for Claude Code specifically. Adapting it to Cursor, Copilot, etc. would require re-architecting the hook system. Not worth it.

## The failure condition

If I catch myself working on Pith for more than **1 hour in any given week**, I stop and ask: what am I avoiding in Auralix?

Pith is done. It does what I need. Every hour I spend on it is an hour not spent on the thing that actually matters.

## The rule for bug fixes

If something breaks my actual workflow (one of the 5 features above), fix it. Time budget: 1 hour. If the fix takes longer, it's probably a sign the feature needs a redesign, not a patch.

If something breaks in a feature I don't use daily, leave it or document it in an issue. Don't fix it unless someone else reports it too.

## What v1.0 represents

A tool I personally use every day that saves real tokens and time. A GitHub repo with honest numbers and a clear status. Not a startup, not a hobby project I'm still actively building — a finished tool.

Shipped is better than perfect. This is shipped.
