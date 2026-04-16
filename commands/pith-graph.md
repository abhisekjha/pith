---
allowed-tools: Bash
description: Run the Pith wiki graph generator for the current project. Scans wiki/ for .md files, extracts [[wikilinks]], and opens an interactive force-directed graph in the browser as wiki-graph.html.
---
Run the Pith wiki graph generator for the current project.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/tools/graph_generator.py"
```

The script will:
1. Scan `./wiki/` for `.md` files and extract `[[wikilinks]]`
2. Write `wiki-graph.html` to the current project root
3. Automatically open it in the default browser

If `wiki/` does not exist or has no `.md` files, tell the user to build the wiki first with `/pith wiki`.
