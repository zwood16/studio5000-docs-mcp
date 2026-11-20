# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

MCP server that indexes the Studio 5000 Logix Designer help (v31–v36 legacy flat-HTM, v37 Oxygen WebHelp) into one local SQLite FTS5 database per version and exposes search/read tools. Python 3.14, uv, official `mcp` SDK v2 (`MCPServer`).

## Commands

```
uv run pytest -m "not integration" -q              # offline unit tests only (synthetic fixtures, no help install)
uv run pytest -s                                   # full suite; -s prints per-query eval results
uv run pytest tests/test_index.py::test_name       # single test
uv run pytest tests/test_eval.py -s                # retrieval eval only (hit@3 and MRR@10 over eval.yaml, eval_holdout.yaml and eval_v36.yaml)
uv run studio5000-docs-mcp --build-only --force-rebuild   # rebuild index, print stats and timing
uv run studio5000-docs-mcp --build-only --all            # build every discovered version, one line each
uv run python scripts/smoke_stdio.py               # start server over stdio, list and call every tool
uv run python scripts/bench.py [--holdout] [--json PATH]   # benchmark build/warm start/size/eval/latency/footprint (needs S5K_HELP_ROOT)
uv run studio5000-docs-mcp                         # run the server (stdio)
```

Offline tests need nothing. Integration tests (marker `integration`: tests using the `manager`/`index`/`tools` fixtures, plus `tests/test_eval.py`) and real builds need a help install: set `S5K_HELP_ROOT` to the `Studio 5000` install folder, the `Logix Designer/ENU` folder or one `rs5000` folder; when it is unset or not found the integration tests skip (there is no fallback path). On the author's Linux box the whole `Studio 5000` install folder is a read-only share at `/mnt/studio5000` (v33–v37: `Logix Designer/ENU/v*/Bin/Help/ENU/rs5000`; v31–v32: `Help/ENU/v*/rs5000`). The v36 eval needs v36 to be discovered. Never write into the help folder. Offline fixtures are made-up content (`tests/fixtures/`, `synthetic_root` in `tests/conftest.py`), never Rockwell text.

Lint and format with ruff (config in `pyproject.toml`): `uv run ruff check` and `uv run ruff format`.

## Architecture

Pipeline: `discovery` → `sources/oxygen` (+ `extract`) → `index.build_index` → `index.Index` queries → `server` tools.

- **`discovery.py`** scans a Studio 5000 install folder's two help trees (`Logix Designer/ENU/v*/Bin/Help/ENU/rs5000` and `Help/ENU/v*/rs5000`; Logix Designer wins on duplicates; versions below `MIN_VERSION` = 31 are dropped; v30 is CHM-only and out of scope) and labels each `oxygen` (has `oxygen-webhelp/`), `legacy` (has `toc.json`) or `unknown`. Default version = requested one if found, else the newest oxygen/legacy one.
- **`sources/base.py`** defines the `HelpSource` protocol (`RawPage`, `TocNode`). The indexer only talks to this protocol, so formats are added as sources. Sources may set `RawPage.section` (else it is derived from the path) and `crumb_trail` (page-header breadcrumb fallback).
- **`sources/oxygen.py`** reads the v37 format: pages (parsed in a process pool), TOC from `oxygen-webhelp/app/nav-links/json/` (root is `nav-links.js`, AMD `define({...})`; other files are `<tocID>.js` with an `oxy_webhelp_navlinks = ` prefix), help IDs from `context-help-map.xml` + `rs5000.csv`, keywords from `index.xml`. The `menu.hasChildren` flag is unreliable, so the loader always looks for a `<tocID>.js` child file.
- **`sources/legacy.py`** reads v31–v36: top-level `*.htm` (nav pages skipped) parsed by `extract.parse_legacy_html` (body is `#content_section`; table-based notes become blockquotes only for allowlisted labels, so icon-legend tables stay tables; legacy rules are gated so Oxygen output is unchanged), TOC from nested `toc.json` (nested `hidden` nodes are kept because thousands of real pages are only reachable through them; hidden top-level entries are dropped), help IDs from 2-column `rs5000.csv`, keywords from `keywords.csv`. Sections are mapped from the TOC top level to the v37 names (`help/rs5000`, `help/clinset`, `help/modules`, `quickstart`, `glossary`, `other`) so `classify`, mnemonics and the section filter behave the same in every version.
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
- **Ranking:** search is tiered: tier 0 is an exact title or lone-mnemonic match, tier 1 is all terms (AND), tier 2 is any term (OR) to fill the limit. bm25 column weights are in `BM25_WEIGHTS`; ALL-CAPS query tokens matching a page mnemonic get a flat `MNEMONIC_BONUS`. Query side (`extract.query_term`): question words (`_STOP`, lower/mixed case only) are dropped, plain-word hyphenated tokens also match their parts, and lower/mixed-case words are expanded through the general-vocabulary `SYNONYMS` table (recall only; no query-specific entries). Multi-word tier 1/2 candidates (top `RESCORE_POOL` by bm25, plus the best `TITLE_POOL` title matches per term in the OR tier) are rescored as bm25 minus `TITLE_COVERAGE_WEIGHT` times the IDF-weighted fraction of the query's original (non-synonym) terms found in the page title; AND hits only compete with the OR pool when they cannot fill the limit. After changing tokenizer, weights or tiers, rerun the eval and report the hit@3 score.
- **Eval expectations:** expected paths must be verified against the actual help content (grep the HTML or SQL `LIKE` on the DB), never taken from search results. Tune only against `tests/eval.yaml` (hit@3 >= 0.7). `tests/eval_holdout.yaml` is held out: never tune against it, and don't edit its queries or expected paths after seeing results (floor hit@3 >= 0.5). `tests/eval_v36.yaml` (floor 0.7, runs on the v36 index) was ported from eval.yaml by mapping each expected v37 page to the same-titled v36 page and checking its content; it may guide tuning like eval.yaml. Report hit@3 and MRR@10 for all three sets after ranking changes.
- **`mcp` dependency:** v2+ (`from mcp.server.mcpserver import MCPServer`). `FastMCP` was renamed in v2 and `mcp.server.fastmcp` now raises on import; v2 client models use snake_case fields (`server_info`, `input_schema`, `is_error`).
- **Versions:** every tool takes `version` ('v33' or a project revision like '33.01'; `normalize_version` keeps the major). The server layer prefixes ids from non-default versions (`v33:14742.htm`, including Markdown `page:` links, TOC node ids and breadcrumbs) and accepts them back; a prefix conflicting with an explicit `version` is an error. Pagination offsets index the stored, unqualified Markdown. Non-default versions build on first use. `list_versions` flags identical help via each source's `content_signature()` (v33 and v34 are byte-identical).
- **Unreachable help folder:** `IndexManager` adds versions that have a usable cached DB (fingerprint suffix matches, stored root under the configured root, matched by name only) as `VersionInfo(cached=True)`. `build()` never rebuilds or fingerprints an unreachable root; it opens the cache or errors. `refresh()` (called from `get`, `list_versions` and `get_help_statistics`) rediscovers at most every `REDISCOVER_AFTER` seconds while nothing or only cached versions were found. `get_image` reads from the help folder, so it needs the share.
- **Renamed mnemonics:** `MNEMONIC_RENAMES_V36` is static on purpose: the v38 LE page says "LES to LE" when it should say LEQ. `get_instruction` tries it in both directions when there's no exact match.
- **Duplicates:** search collapses hits with the same lower(title) and `content_hash` (sha1 of the final Markdown) into the best-ranked one (`(+N identical copies)`); other tools list duplicates deliberately.
- **Windows:** with `S5K_HELP_ROOT` unset, `discovery.py` probes `C:\Program Files (x86)\Rockwell Software\Studio 5000` (and the `Program Files` equivalent); confirmed on Windows 11 with v31–v37 found from both help trees.
- **No Rockwell content in the repo:** never commit extracted Rockwell help content or fixtures. Each user builds the index from their own licensed install.
