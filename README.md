# studio5000-docs-mcp

MCP server that lets Claude (Claude Code, VS Code Chat, etc.) search and read the Studio 5000 Logix Designer help: instructions, fault codes, module configuration, dialogs.

It indexes the Oxygen WebHelp that ships with Logix Designer **v37** into a local SQLite FTS5 database (BM25 ranking, title/mnemonic/keyword boosting, clean Markdown per page with tables preserved). Older versions (v33-v36, a legacy flat `.htm` format) are detected but not indexed yet.

Each user builds the index locally from their own licensed install. No Rockwell content is stored in this repository.

## Install / run

```
uv run studio5000-docs-mcp
uvx --from git+<repo-url> studio5000-docs-mcp
```

Register with Claude Code:

```
claude mcp add s5k-help -e S5K_HELP_ROOT="/path/to/Logix Designer/ENU" -- uv --directory <path-to-this-repo> run studio5000-docs-mcp
```

The first start builds the index in the background (a few seconds to about a minute depending on disk or network speed); tools wait for it. Later starts reuse the cache unless the help files changed.

## Configuration

| Env var | CLI flag | Meaning |
|---|---|---|
| `S5K_HELP_ROOT` | `--root` | Either one `rs5000` help folder, or the `Logix Designer/ENU` folder (scanned for `v*/Bin/Help/ENU/rs5000`). |
| `S5K_VERSION` | `--version` | Default version, e.g. `v37` (default: highest Oxygen-format version found). |
| `S5K_DB_DIR` | | Where index databases are stored (default: user cache dir for `studio5000-docs-mcp`). |
| | `--force-rebuild` | Rebuild even if the cache is fresh. |
| | `--transport stdio\|streamable-http` | Default `stdio`. |
| | `--build-only` | Build the index, print statistics and timing, exit. |

Windows: when `S5K_HELP_ROOT` is unset, the server probes `%ProgramFiles(x86)%\Rockwell Software\Studio 5000\Logix Designer\ENU\v*\Bin\Help\ENU\rs5000` and the same path under `%ProgramFiles%`. **This default path is unverified; it has not been checked on a real Windows install.** Set `S5K_HELP_ROOT` if it does not match.

The index is one SQLite file per version. It is rebuilt when the help folder's HTML files, TOC files or metadata files (`context-help-map.xml`, `rs5000.csv`, `index.xml`) change, or when the schema version changes. The help folder is only ever read.

## Tools

`search_help`, `get_page` (paginated with `offset`/`max_chars`), `get_instruction` (by mnemonic, e.g. `TON`, `SSV`), `get_page_by_help_id`, `browse_toc`, `get_breadcrumb`, `list_versions`, `get_help_statistics`. Search snippets are not page content; the server instructions tell the model to call `get_page`.

## Development

```
uv run pytest -m "not integration" -q   # offline unit tests, no help install needed
uv run pytest -s          # full suite; integration tests need S5K_HELP_ROOT and are skipped otherwise
uv run python scripts/smoke_stdio.py   # start the server over stdio and call each tool
```

`tests/eval.yaml` holds retrieval queries with expected pages; `tests/test_eval.py` reports hit@3.
