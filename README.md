# studio5000-docs-mcp

MCP server that lets agents (Claude Code, VS Code Chat, etc.) search and read the Studio 5000 Logix Designer help: instructions, fault codes, module configuration, dialogs, etc.

It indexes the help that ships with Logix Designer **v31–v37+** into a local SQLite FTS5 database, one per version (BM25 ranking, title/mnemonic/keyword boosting, clean Markdown per page with tables preserved).

Each user builds the index locally from their own licensed install. No Rockwell content is stored in this repository.

> [!NOTE]
> Versions older than v31 are currently unsupported as they use a different format for the help docs
>
> Only versions up to v38 have been tested so far, but newer versions that use the current Oxygen WebHelp format should work just fine.

**Related Projects**

- [l5x-mcp](https://github.com/zwood16/l5x-mcp): an MCP server for reading and writing L5X files
- [py_l5x](https://github.com/zwood16/py_l5x): a Python L5X parser
- [ACD Auto Export](https://github.com/zwood16/acd-auto-export): a companion script that automatically exports your active Studio 5000 project (`.acd` file) to the more usable `.l5x` file format using the Studio 5000 SDK every time you save.

## Install

[uv](https://docs.astral.sh/uv/getting-started/installation/) is strongly recommended. It downloads Python 3.14 automatically if you don't have it.

### Option 1: release wheel (no clone)

Each GitHub release has a Python wheel attached. Copy its URL from the [releases](https://github.com/zwood16/studio5000-docs-mcp/releases) page. The examples below use v1.0.

**Run it with `uvx`.** This is the simplest way. uv downloads the wheel and its dependencies into its cache on first use and reuses them after that:

```
uvx --from https://github.com/zwood16/studio5000-docs-mcp/releases/download/v1.0/studio5000_docs_mcp-1.0.0-py3-none-any.whl studio5000-docs-mcp --help
```

**Or install it as a tool.** This puts `studio5000-docs-mcp` on your PATH, and starts don't need the network:

```
uv tool install https://github.com/zwood16/studio5000-docs-mcp/releases/download/v1.0/studio5000_docs_mcp-1.0.0-py3-none-any.whl
studio5000-docs-mcp --help
```

To upgrade, run `uv tool install --force <new wheel URL>`. To remove, run `uv tool uninstall studio5000-docs-mcp`.

### Option 2: from source

```
git clone https://github.com/zwood16/studio5000-docs-mcp
cd studio5000-docs-mcp
uv run studio5000-docs-mcp --help
```

To run the latest unreleased code without cloning, use `uvx --from git+https://github.com/zwood16/studio5000-docs-mcp studio5000-docs-mcp`.

### Prebuild the indexes (optional)

To build every version's index before first use, run the tool with `--build-only --all`, e.g. `studio5000-docs-mcp --build-only --all` after `uv tool install`. Otherwise each index is built the first time it's needed (see [First start](#first-start)).

## Register the MCP Server

The server needs to know where Studio 5000 is installed. Set `S5K_HELP_ROOT` to the `Studio 5000` install folder (see [Configuration](#configuration) for other accepted folders). On Windows with a default install (`C:\Program Files (x86)\Rockwell Software\Studio 5000`), you can leave it out.

### Claude Code

Use the command that matches how you installed the tool.

With `uvx` and the release wheel:

```
claude mcp add s5k-help -e S5K_HELP_ROOT="/path/to/Studio 5000" -- uvx --from https://github.com/zwood16/studio5000-docs-mcp/releases/download/v1.0/studio5000_docs_mcp-1.0.0-py3-none-any.whl studio5000-docs-mcp
```

With `uv tool install`:

```
claude mcp add s5k-help -e S5K_HELP_ROOT="/path/to/Studio 5000" -- studio5000-docs-mcp
```

From a clone of the repository:

```
claude mcp add s5k-help -e S5K_HELP_ROOT="/path/to/Studio 5000" -- uv --directory <path-to-this-repo> run studio5000-docs-mcp
```

### Other MCP clients

Other clients (e.g. VS Code `.vscode/mcp.json`) need the same command, arguments and environment. For the `uvx` setup:

```json
{
    "servers": {
        "s5k-help": {
            "type": "stdio",
            "command": "uvx",
            "args": [
                "--from",
                "https://github.com/zwood16/studio5000-docs-mcp/releases/download/v1.0/studio5000_docs_mcp-1.0.0-py3-none-any.whl",
                "studio5000-docs-mcp"
            ],
            "env": { "S5K_HELP_ROOT": "/path/to/Studio 5000" }
        }
    }
}
```

With `uv tool install`, use `"command": "studio5000-docs-mcp"` and no `args`.

## First start

The first start builds the default (newest) version's index in the background. That takes a few seconds to about a minute, depending on disk or network speed, and tools wait for it. Other versions build the first time a tool asks for them. Later starts reuse the cache unless the help files changed. The cache lives in your user cache directory, not in the install, so upgrading the wheel keeps it; a new schema version rebuilds it automatically.

## Configuration

| Env var         | CLI flag                             | Meaning                                                                                                                                                                                                         |
| --------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `S5K_HELP_ROOT` | `--root`                             | The `Studio 5000` install folder, the `Logix Designer/ENU` folder, or one `rs5000` help folder. Folders are scanned for `Logix Designer/ENU/v*/Bin/Help/ENU/rs5000` (v33+) and `Help/ENU/v*/rs5000` (v31, v32). |
| `S5K_VERSION`   | `--version`                          | Default version, e.g. `v37` (default: the newest version found; a requested version that is not found falls back to it with a warning).                                                                         |
| `S5K_DB_DIR`    |                                      | Where index databases are stored (default: user cache dir for `studio5000-docs-mcp`).                                                                                                                           |
|                 | `--force-rebuild`                    | Rebuild even if the cache is fresh.                                                                                                                                                                             |
|                 | `--transport stdio\|streamable-http` | Default `stdio`.                                                                                                                                                                                                |
|                 | `--build-only`                       | Build the index, print statistics and timing, exit.                                                                                                                                                             |
|                 | `--all`                              | With `--build-only`: build every discovered version, one summary line each (exit 1 if any fails).                                                                                                               |

**On Windows**:  
when `S5K_HELP_ROOT` is unset, the server probes `%ProgramFiles(x86)%\Rockwell Software\Studio 5000` (both the `Logix Designer\ENU\v*\Bin\Help\ENU\rs5000` and `Help\ENU\v*\rs5000` trees) and the same folder under `%ProgramFiles%`. This matches a default Studio 5000 install (checked on Windows 11 with v30–v38 installed under `Program Files (x86)`). Set `S5K_HELP_ROOT` for non-default install locations.

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
