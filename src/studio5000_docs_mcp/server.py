"""FastMCP server: tool definitions and CLI entry point."""

from __future__ import annotations

import asyncio
import functools
import json
import sys
import time

from mcp.server.fastmcp import FastMCP

from .config import Config, load_config
from .index import Index, IndexManager, IndexNotReady

INSTRUCTIONS = """\
Studio 5000 Logix Designer help (Rockwell, Oxygen WebHelp) as a searchable index.

CRITICAL: search_help snippets are NOT documentation content. Never answer from snippets alone; always call get_page (or get_instruction) to read the actual page before answering.

RESEARCH WORKFLOW
1. search_help - find pages by keyword. Returns page_id, title, kind, breadcrumb and a short snippet only.
2. get_page - read the full Markdown (tables preserved; long pages are paginated with offset/max_chars). Pick results using the breadcrumb: many titles are duplicated (e.g. several 'Options command' or 'Get System Value (GSV) and Set System Value (SSV)' pages), and the breadcrumb tells you which product area each belongs to.
3. REPEAT with synonyms or different identifiers (complex questions usually need 2-5 page reads). Follow [text](page:<page_id>) links in page Markdown with get_page.
4. When the user names an instruction mnemonic (TON, MAOC, GSV, SSV, ...), prefer get_instruction.
5. get_page_by_help_id resolves numeric or named context-help IDs. browse_toc and get_breadcrumb explore structure.

TIPS
- Identifiers work well: '.DN', 'TON', '1756-IF16', 'Axis_CIP_Drive', fault 'type 4 code 34'.
- Use kind (instruction, fault, module, dialog, command, concept) or section filters to narrow results.
- get_help_statistics shows index build progress if results are empty or a tool says the index is still building.
"""

mcp = FastMCP("studio5000-docs", instructions=INSTRUCTIONS)

_manager: IndexManager | None = None


def set_manager(m: IndexManager) -> None:
    global _manager
    _manager = m


def _index(version: str | None) -> Index:
    if _manager is None:
        raise RuntimeError("server not initialised")
    return _manager.get(version)


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (ValueError, IndexNotReady, RuntimeError, KeyError) as e:
            return f"Error: {e.args[0] if e.args else e}"

    return wrapper


def _crumb(row) -> str:
    return row["breadcrumb"] or "(top level)"


def _page_text(idx: Index, row, offset: int, max_chars: int, with_links: bool = True) -> str:
    md: str = row["markdown"]
    offset = max(0, int(offset))
    max_chars = max(500, min(int(max_chars), 100000))
    chunk = md[offset : offset + max_chars]
    end = offset + len(chunk)
    head = [
        f"page_id: {row['id']}",
        f"title: {row['title']}",
        f"path: {row['path']}",
        f"kind: {row['kind']}   section: {row['section']}",
        f"breadcrumb: {_crumb(row)}",
        f"version: {row['version']}   chars: {row['chars']}",
        "",
    ]
    out = "\n".join(head) + chunk
    if offset == 0 and with_links:
        ids = json.loads(row["links"] or "[]")
        if ids:
            titles = idx.titles_for(ids[:100])
            lines = [f"- {titles[i][0]}  (page_id: {i})" for i in ids[:100] if i in titles]
            if lines:
                more = f"\n(+{len(ids) - 100} more)" if len(ids) > 100 else ""
                out += "\n\n---\nOutgoing links:\n" + "\n".join(lines) + more
    if end < len(md):
        out += (f"\n\n[Truncated: showing chars {offset}-{end} of {len(md)}. "
                f"Call get_page again with offset={end} to continue.]")
    elif offset > 0:
        out += f"\n\n[End of page: chars {offset}-{end} of {len(md)}.]"
    return out


# ------------------------------------------------------------------ tools

@_guard
def search_help(query: str, version: str | None = None, section: str | None = None,
                kind: str | None = None, limit: int = 10) -> str:
    """Search the Logix Designer help. Returns page_id, title, kind, breadcrumb and a short snippet per hit.

    Snippets are NOT page content: call get_page with a page_id to read the page.
    kind: instruction | fault | module | dialog | command | concept.
    section: e.g. 'clinset' (instruction set), 'rs5000' (IDE), 'modules' (I/O modules), 'common-content'.
    """
    idx = _index(version)
    if kind and kind not in ("instruction", "fault", "module", "dialog", "command", "concept"):
        return "Error: kind must be one of instruction, fault, module, dialog, command, concept."
    res = idx.search(query, kind=kind, section=section, limit=limit)
    if not res:
        return f"No results for {query!r} in {idx.version}. Try synonyms, a mnemonic, or fewer terms."
    lines = [f"{len(res)} result(s) for {query!r} in {idx.version}. Snippets are not content; call get_page.", ""]
    for i, r in enumerate(res, 1):
        mn = f" [{r['mnemonic']}]" if r["mnemonic"] else ""
        lines.append(f"{i}. {r['title']}{mn}  ({r['kind']})")
        lines.append(f"   page_id: {r['id']}")
        lines.append(f"   breadcrumb: {r['breadcrumb'] or '(top level)'}")
        snip = " ".join((r["snippet"] or "").split())
        if snip:
            lines.append(f"   snippet: {snip}")
    return "\n".join(lines)


@_guard
def get_page(page_id: str, version: str | None = None, offset: int = 0, max_chars: int = 20000) -> str:
    """Get a page as Markdown plus its outgoing links. page_id may be a page_id (e.g. ait_455128) or a help-root-relative path.

    Long pages are paginated: pass the offset from the 'next offset' hint to continue. max_chars is clamped to 500-100000.
    """
    idx = _index(version)
    row = idx.resolve_page(page_id)
    if row is None:
        return f"Error: no page {page_id!r} in {idx.version}. Use search_help to find page_ids."
    return _page_text(idx, row, offset, max_chars)


@_guard
def get_instruction(mnemonic: str, version: str | None = None) -> str:
    """Look up an instruction page directly by mnemonic, e.g. TON, MAOC, GSV, SSV. Prefers the instruction-set (clinset) section."""
    idx = _index(version)
    rows = idx.instruction(mnemonic)
    if not rows:
        res = idx.search(mnemonic, kind="instruction", limit=5)
        if not res:
            return f"No instruction with mnemonic {mnemonic!r} in {idx.version}. Try search_help."
        lines = [f"No exact mnemonic {mnemonic!r}. Closest instruction pages:"]
        lines += [f"- {r['title']}  (page_id: {r['id']})  {r['breadcrumb']}" for r in res]
        return "\n".join(lines)
    best = rows[0]
    out = _page_text(idx, best, 0, 20000)
    if len(rows) > 1:
        out += "\n\n---\nOther pages with this mnemonic:\n" + "\n".join(
            f"- {r['title']}  (page_id: {r['id']})  {r['breadcrumb']}" for r in rows[1:11])
    return out


@_guard
def get_page_by_help_id(help_id: str, version: str | None = None) -> str:
    """Look up the page for an F1 / context help ID (numeric like 131173, or named like about_the_status_bar)."""
    idx = _index(version)
    rows = idx.by_help_id(help_id)
    if not rows:
        return f"No page for help id {help_id!r} in {idx.version}."
    out = _page_text(idx, rows[0], 0, 20000)
    if len(rows) > 1:
        out += "\n\n---\nOther pages with this help id:\n" + "\n".join(
            f"- {r['title']}  (page_id: {r['id']})" for r in rows[1:])
    return out


@_guard
def browse_toc(node_id: str | None = None, version: str | None = None) -> str:
    """List table-of-contents children. Omit node_id for the top level; node_id may be a TOC node id or a page_id."""
    idx = _index(version)
    try:
        kids = idx.toc_children(node_id)
    except KeyError:
        return f"Error: {node_id!r} is not a TOC node or a page in the TOC."
    if not kids:
        return "No children."
    lines = [f"{len(kids)} item(s) in {idx.version} TOC" + (f" under {node_id}" if node_id else "") + ":"]
    for k in kids:
        pg = f"  page_id: {k['page_id']}" if k["page_id"] else ""
        ch = f"  [{k['children']} children]" if k["children"] else ""
        lines.append(f"- {k['title']}  node_id: {k['node_id']}{pg}{ch}")
    return "\n".join(lines)


@_guard
def get_breadcrumb(page_id: str, version: str | None = None) -> str:
    """Get a page's ancestors in the help hierarchy (root first), with page_ids where the ancestor is a page."""
    idx = _index(version)
    row = idx.resolve_page(page_id)
    if row is None:
        return f"Error: no page {page_id!r} in {idx.version}."
    crumbs = json.loads(row["crumbs"] or "[]")
    lines = [f"{row['title']}  (page_id: {row['id']})"]
    if not crumbs:
        lines.append("(no ancestors recorded; top-level or unlisted page)")
    for depth, (title, pid) in enumerate(crumbs):
        lines.append(f"{'  ' * depth}> {title}" + (f"  (page_id: {pid})" if pid else ""))
    return "\n".join(lines)


@_guard
def list_versions() -> str:
    """List discovered help versions, their format, and index state."""
    m = _manager
    if m is None or not m.versions:
        return ("No Studio 5000 help folders found. Set S5K_HELP_ROOT to the 'Logix Designer/ENU' folder "
                "or an 'rs5000' help folder.")
    lines = []
    for v, info in m.versions.items():
        st = m.state(v)
        flag = " (default)" if v == m.default else ""
        fmt = info.format + ("" if info.format == "oxygen" else " - not supported yet")
        pages = f", {st.stats.get('pages')} pages" if st.phase == "ready" else ""
        lines.append(f"- {v}{flag}: format={fmt}, index={st.phase}{pages}, root={info.root}")
    return "\n".join(lines)


@_guard
def get_help_statistics(version: str | None = None) -> str:
    """Index statistics and build state (does not wait for a running build)."""
    m = _manager
    if m is None or not m.versions:
        return list_versions()
    from .config import normalize_version

    v = normalize_version(version) if version else m.default
    if v not in m.versions:
        return f"Error: unknown version {version!r}. Available: {', '.join(m.versions)}"
    st = m.state(v)
    lines = [f"version: {v}", f"build state: {st.phase}" + (f" ({st.done}/{st.total})" if st.total else "")]
    if st.error:
        lines.append(f"error: {st.error}")
    if v in m._indexes:
        s = m._indexes[v].stats()
        lines += [
            f"pages: {s['pages']}  (with breadcrumb: {s['with_breadcrumb']})",
            f"toc nodes: {s['toc_nodes']}  help ids: {s['help_ids']}  mnemonics: {s['mnemonics']}",
            "kinds: " + ", ".join(f"{k}={n}" for k, n in sorted(s["kinds"].items())),
            "sections: " + ", ".join(f"{k}={n}" for k, n in list(s["sections"].items())[:12]),
            f"help root: {s['root']}",
            f"built: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s['built_at']))}  "
            f"(last build {s['build_stats'].get('build_seconds')} s)",
        ]
    return "\n".join(lines)


TOOLS = [search_help, get_page, get_instruction, get_page_by_help_id, browse_toc, get_breadcrumb,
         list_versions, get_help_statistics]


def _async_tool(fn):
    """Register a sync tool as async so waiting for an index build never blocks the event loop."""

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        return await asyncio.to_thread(fn, *a, **kw)

    return wrapper


for _fn in TOOLS:
    mcp.tool()(_async_tool(_fn))


# ------------------------------------------------------------------ CLI

def _build_only(m: IndexManager) -> int:
    if not m.default:
        print("No Oxygen-format help folder found. Set S5K_HELP_ROOT or pass --root.", file=sys.stderr)
        return 2
    t0 = time.time()
    m.build(m.default)
    dt = time.time() - t0
    st = m.state(m.default)
    print(f"version: {m.default}  rebuilt: {st.rebuilt}  elapsed: {dt:.2f} s  db: {m.db_path(m.default)}")
    print(get_help_statistics(m.default))
    return 0


def main(argv: list[str] | None = None) -> None:
    cfg: Config = load_config(argv)
    m = IndexManager(cfg)
    set_manager(m)
    if cfg.build_only:
        raise SystemExit(_build_only(m))
    if not m.versions:
        print("studio5000-docs-mcp: no help folders found (set S5K_HELP_ROOT); tools will report this.",
              file=sys.stderr)
    m.start_background()
    mcp.run(transport=cfg.transport)


if __name__ == "__main__":
    main()
