# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

MCP server that indexes the Studio 5000 Logix Designer help (Oxygen WebHelp, v37) into a local SQLite FTS5 database and exposes search/read tools. Python 3.14, uv, official `mcp` SDK v2 (`MCPServer`).

## Commands

```
uv run pytest -m "not integration" -q              # offline unit tests only (synthetic fixtures, no help install)
uv run pytest -s                                   # full suite; -s prints per-query eval results
uv run pytest tests/test_index.py::test_name       # single test
uv run pytest tests/test_eval.py -s                # retrieval eval only (hit@3 and MRR@10 over eval.yaml and eval_holdout.yaml)
uv run studio5000-docs-mcp --build-only --force-rebuild   # rebuild index, print stats and timing
uv run python scripts/smoke_stdio.py               # start server over stdio, list and call every tool
uv run studio5000-docs-mcp                         # run the server (stdio)
```

Offline tests need nothing. Integration tests (marker `integration`: tests using the `manager`/`index`/`tools` fixtures, plus `tests/test_eval.py`) and real builds need a help install: set `S5K_HELP_ROOT` to the `Logix Designer/ENU` folder or one `rs5000` folder; when it is unset or not found the integration tests skip (there is no fallback path). On the author's Linux box the help is on a read-only share at `/mnt/studio5000/Logix Designer/ENU` (v37 root: `.../v37/Bin/Help/ENU/rs5000`). Never write into the help folder. Offline fixtures are made-up content (`tests/fixtures/`, `synthetic_root` in `tests/conftest.py`), never Rockwell text.

No linter or formatter is configured.

## Architecture

Pipeline: `discovery` → `sources/oxygen` (+ `extract`) → `index.build_index` → `index.Index` queries → `server` tools.

- **`discovery.py`** finds `v*/Bin/Help/ENU/rs5000` folders and labels each `oxygen` (has `oxygen-webhelp/`) or `legacy` (v33–v36, flat `.htm`; detected but not indexed). Default version = highest Oxygen one.
- **`sources/base.py`** defines the `HelpSource` protocol (`RawPage`, `TocNode`). The indexer only talks to this protocol, so a legacy adapter can be added as another source without touching `index.py`.
- **`sources/oxygen.py`** reads the v37 format: pages (parsed in a process pool), TOC from `oxygen-webhelp/app/nav-links/json/` (root is `nav-links.js`, AMD `define({...})`; other files are `<tocID>.js` with an `oxy_webhelp_navlinks = ` prefix), help IDs from `context-help-map.xml` + `rs5000.csv`, keywords from `index.xml`. The `menu.hasChildren` flag is unreliable, so the loader always looks for a `<tocID>.js` child file.
- **`extract.py`** is a hand-written selectolax (`selectolax.lexbor`; the old `parser` module raises on import) `<article>` → Markdown converter that keeps tables, notes and images. Intra-doc links are emitted as `[text](page:<path>)` placeholders, later rewritten to page IDs in `build_index`. It also holds the FTS text helpers (`clean_punct`, `expand_identifiers`, `query_tokens`, `is_stop`).
- **`index.py`**:
  - `build_index` assigns page IDs, builds breadcrumbs (TOC → `DC.relation` chain → CSV toc-path fallback), extracts mnemonics, classifies `kind`, and writes to a temp file before an atomic rename.
  - `Index` serves queries over one read-only connection guarded by an `RLock`.
  - `IndexManager` builds the default version in a background thread at startup; tools wait up to 45 s for it, then return a "still building" message.
- **`server.py`** holds the `MCPServer` app, the `INSTRUCTIONS` string (research workflow: snippets are not content, always call `get_page`) and the CLI. Tools are sync functions wrapped with `_guard` (expected errors are re-raised as `ToolError`, which the client sees as `is_error=true` with an "Error executing tool <name>: " prefix; empty-but-valid outcomes like zero search hits stay normal text) and registered as async via `asyncio.to_thread`.

## Non-obvious details

- **Page IDs:** `page_id` is `DC.identifier` (e.g. `ait_455128`) only when unique; about 820 pages share identifiers (duplicate pages such as the six GSV/SSV pages) and use their help-root-relative path instead. Lookups accept either form.
- **Cache invalidation:** One DB per version in the platformdirs user cache (or `S5K_DB_DIR`). The stored fingerprint combines the source fingerprint (HTML file count + max mtime, size/mtime of `context-help-map.xml`, `rs5000.csv`, `index.xml`, and nav-links JSON count + max mtime), `SCHEMA_VERSION`, the root path and the `TOKENIZER` string. **Bump `SCHEMA_VERSION` whenever you change the schema or anything that changes indexed content**, or existing caches will be served stale.
- **Tokenizer and query handling must stay symmetric:** The FTS tokenizer is `porter unicode61 ... tokenchars '_.-'` so identifiers like `.DN`, `1756-IF16` and `Axis_CIP_Drive` stay whole. Because that also glues sentence periods onto words, `clean_punct` strips trailing `.-_` and `expand_identifiers` adds the parts of dotted/hyphenated identifiers to an `extra` column. Index-time and query-time processing must match. Raw queries are quoted per token and must never raise FTS syntax errors (there is a test with hostile queries).
- **Ranking:** search is tiered: tier 0 is an exact title or lone-mnemonic match, tier 1 is all terms (AND), tier 2 is any term (OR) to fill the limit. bm25 column weights are in `BM25_WEIGHTS`; ALL-CAPS query tokens matching a page mnemonic get a flat `MNEMONIC_BONUS`. After changing tokenizer, weights or tiers, rerun the eval and report the hit@3 score.
- **Eval expectations:** expected paths must be verified against the actual help content (grep the HTML or SQL `LIKE` on the DB), never taken from search results. Tune only against `tests/eval.yaml` (hit@3 >= 0.7). `tests/eval_holdout.yaml` is held out: never tune against it, and don't edit its queries or expected paths after seeing results (floor hit@3 >= 0.5). Report both hit@3 and MRR@10 for both sets after ranking changes.
- **`mcp` dependency:** v2+ (`from mcp.server.mcpserver import MCPServer`). `FastMCP` was renamed in v2 and `mcp.server.fastmcp` now raises on import; v2 client models use snake_case fields (`server_info`, `input_schema`, `is_error`).
- **Windows default probe path** in `discovery.py` is unverified on a real install.
- **No Rockwell content in the repo:** never commit extracted Rockwell help content or fixtures. Each user builds the index from their own licensed install.
