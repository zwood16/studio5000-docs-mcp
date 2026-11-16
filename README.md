# studio5000-docs-mcp

MCP server that lets Claude (Claude Code, VS Code Chat, etc.) search and read the Studio 5000 Logix Designer help: instructions, fault codes, module configuration, dialogs.

It indexes the help that ships with Logix Designer **v31–v37** (v31–v36 use a legacy flat `.htm` format with `toc.json`; v37 is Oxygen WebHelp) into a local SQLite FTS5 database, one per version (BM25 ranking, title/mnemonic/keyword boosting, clean Markdown per page with tables preserved).

Each user builds the index locally from their own licensed install. No Rockwell content is stored in this repository.

## Install / run

```
uv run studio5000-docs-mcp
uvx --from git+https://github.com/zwood16/studio5000-docs-mcp studio5000-docs-mcp
```

Register with Claude Code:

```
claude mcp add s5k-help -e S5K_HELP_ROOT="/path/to/Logix Designer/ENU" -- uv --directory <path-to-this-repo> run studio5000-docs-mcp
```

The first start builds the index in the background (a few seconds to about a minute depending on disk or network speed); tools wait for it. Later starts reuse the cache unless the help files changed.

## Configuration

| Env var | CLI flag | Meaning |
|---|---|---|
| `S5K_HELP_ROOT` | `--root` | The `Studio 5000` install folder, the `Logix Designer/ENU` folder, or one `rs5000` help folder. Folders are scanned for `Logix Designer/ENU/v*/Bin/Help/ENU/rs5000` (v33+) and `Help/ENU/v*/rs5000` (v31, v32). |
| `S5K_VERSION` | `--version` | Default version, e.g. `v37` (default: the newest version found; a requested version that is not found falls back to it with a warning). |
| `S5K_DB_DIR` | | Where index databases are stored (default: user cache dir for `studio5000-docs-mcp`). |
| | `--force-rebuild` | Rebuild even if the cache is fresh. |
| | `--transport stdio\|streamable-http` | Default `stdio`. |
| | `--build-only` | Build the index, print statistics and timing, exit. |
| | `--all` | With `--build-only`: build every discovered version, one summary line each (exit 1 if any fails). |

Windows: when `S5K_HELP_ROOT` is unset, the server probes `%ProgramFiles(x86)%\Rockwell Software\Studio 5000` (both the `Logix Designer\ENU\v*\Bin\Help\ENU\rs5000` and `Help\ENU\v*\rs5000` trees) and the same folder under `%ProgramFiles%`. This matches a default Studio 5000 install (checked on Windows 11 with v30–v37 installed under `Program Files (x86)`). Set `S5K_HELP_ROOT` for non-default install locations.

The index is one SQLite file per version. It is rebuilt when the help folder's HTML files, TOC files or metadata files (`context-help-map.xml`, `rs5000.csv`, `index.xml`) change, or when the schema version changes. The help folder is only ever read.

## Multiple versions

- Discovery: `Logix Designer/ENU/v*/Bin/Help/ENU/rs5000` (v33+) and `Help/ENU/v*/rs5000` (v31, v32) under the `Studio 5000` install folder.
- The default is the newest version found. Every tool takes an optional `version`: `'v33'` or a project revision such as `'33.01'`.
- Page and TOC node ids from a non-default version are prefixed (`v33:14742.htm`) and are accepted back as-is, so a prefixed id selects its version.
- Non-default versions build on first use (a few seconds each) and are cached. `uv run studio5000-docs-mcp --build-only --all` prebuilds all of them.
- `list_versions` flags versions whose help content is identical to an earlier one (e.g. `same help content as v33`).

## Tools

`search_help`, `get_page` (paginated with `offset`/`max_chars`), `get_instruction` (by mnemonic, e.g. `TON`, `SSV`), `get_page_by_help_id`, `browse_toc`, `get_breadcrumb`, `list_versions`, `get_help_statistics`. Search snippets are not page content; the server instructions tell the model to call `get_page`.

- **Page IDs:** a page's `page_id` is its help topic ID (e.g. `ait_455128`) when that ID is unique. Some pages share a topic ID (duplicated pages such as the GSV/SSV pages); those use their help-relative path instead (e.g. `help/clinset/...html`). Every tool that takes a `page_id` accepts either form.
- **Errors:** failures (unknown page, TOC node or version, invalid `kind`, index still building) are returned as MCP tool errors (`is_error: true`). Valid empty outcomes, such as no search hits or an unmapped help ID, are normal results.

## Development

```
uv run pytest -m "not integration" -q   # offline unit tests, no help install needed
uv run pytest -s          # full suite; integration tests need S5K_HELP_ROOT and are skipped otherwise
uv run python scripts/smoke_stdio.py   # start the server over stdio and call each tool
```

`tests/eval.yaml` holds retrieval queries with expected pages; `tests/test_eval.py` reports hit@3.
