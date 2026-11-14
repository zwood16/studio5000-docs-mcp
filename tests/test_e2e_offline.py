"""End-to-end build and query over a synthetic tree. Trees under 200 files are parsed serially, so no
multiprocessing pool is involved."""

import asyncio
import json
import os
import re
import sqlite3

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp import index as index_mod
from studio5000_docs_mcp.index import IndexManager, build_index
from studio5000_docs_mcp.sources.oxygen import OxygenSource


@pytest.fixture()
def idx(synthetic_root, tmp_path):
    m = IndexManager(Config(help_root=synthetic_root, db_dir=tmp_path / "db"))
    assert m.default == "v99"
    m.build("v99")
    yield m.get()
    m.get().close()


def test_stats(idx):
    s = idx.stats()
    assert s["pages"] == 4 and s["toc_nodes"] == 2 and s["help_ids"] == 3 and s["mnemonics"] == 1


def test_search(idx):
    res = idx.search("zebra")
    assert [r["id"] for r in res] == ["ait_3"]
    assert idx.search("WCT")[0]["id"] == "ait_2"  # lone mnemonic goes to its instruction page
    assert idx.search("Widget Counter (WCT)")[0]["id"] == "ait_2"
    assert idx.search("frobnicate")[0]["id"] == "ait_1"  # keyword-index term
    assert idx.search("Timer.DN")[0]["id"] == "ait_2"
    assert idx.search("dn")[0]["id"] == "ait_2"  # part of identifier via extra column
    assert idx.search("nonexistentterm") == []
    assert idx.search("zebra", kind="instruction") == []
    assert idx.search("widget", kind="instruction")[0]["id"] == "ait_2"


def test_get_by_help_id(idx):
    assert [r["id"] for r in idx.by_help_id("123")] == ["ait_2"]  # xml
    assert [r["id"] for r in idx.by_help_id("456")] == ["ait_1"]  # csv
    assert idx.by_help_id("999") == []


def test_resolve_and_instruction(idx):
    assert idx.resolve_page("help/clinset/wct.html")["id"] == "ait_2"
    assert idx.resolve_page("ait_2")["path"] == "help/clinset/wct.html"
    assert idx.instruction("wct")[0]["id"] == "ait_2"


def test_breadcrumbs(idx):
    assert json.loads(idx.resolve_page("ait_2")["crumbs"]) == [["Widget Overview", "ait_1"]]  # TOC chain
    assert idx.resolve_page("ait_2")["breadcrumb"] == "Widget Overview"
    assert idx.resolve_page("ait_3")["breadcrumb"] == "Widget Overview"  # DC.relation fallback
    assert idx.resolve_page("ait_4")["breadcrumb"] == "Csv Section > Deeper"  # CSV fallback
    assert idx.resolve_page("ait_1")["breadcrumb"] == ""


def test_link_rewriting(idx):
    page = idx.resolve_page("ait_1")
    md = page["markdown"]
    assert "[counter](page:ait_2)" in md
    assert "nowhere" in md and "missing.html" not in md  # unresolved link keeps text only
    assert not re.search(r"page:help/", md)
    assert json.loads(page["links"]) == ["ait_2"]


def test_toc_children(idx):
    top = idx.toc_children(None)
    assert [(n["node_id"], n["children"]) for n in top] == [("ait_1-d1", 1)]
    assert idx.toc_children("ait_1-d1")[0]["page_id"] == "ait_2"


# ---------------- cache invalidation and retry ----------------

def _mgr(root, tmp_path):
    return IndexManager(Config(help_root=root, db_dir=tmp_path / "db"))


def _build(root, tmp_path):
    m = _mgr(root, tmp_path)
    m.build("v99")
    rebuilt = m.state("v99").rebuilt
    m.get().close()
    return rebuilt


def _bump(path, size_change=False):
    if size_change:
        with open(path, "a", encoding="utf-8") as f:
            f.write(" ")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_cache_reused_when_unchanged(synthetic_root, tmp_path):
    assert _build(synthetic_root, tmp_path) is True
    assert _build(synthetic_root, tmp_path) is False


@pytest.mark.parametrize("rel,size", [
    ("help/a.html", False),
    ("index.xml", False),
    ("index.xml", True),
    ("rs5000.csv", False),
    ("context-help-map.xml", False),
    ("oxygen-webhelp/app/nav-links/json/ait_1-d1.js", False),
    ("oxygen-webhelp/app/nav-links/json/ait_1-d1.js", True),
])
def test_rebuild_when_source_changes(synthetic_root, tmp_path, rel, size):
    assert _build(synthetic_root, tmp_path) is True
    _bump(synthetic_root / rel, size)
    assert _build(synthetic_root, tmp_path) is True
    assert _build(synthetic_root, tmp_path) is False


def test_build_index_stores_given_fingerprint(synthetic_root, tmp_path):
    src = OxygenSource(synthetic_root, "v99")
    db = tmp_path / "x.sqlite"
    build_index(src, db, fingerprint="custom-fp")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()[0] == "custom-fp"
    conn.close()


def test_failed_background_build_retry(synthetic_root, tmp_path, monkeypatch):
    real = index_mod.build_index
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return real(*a, **kw)

    monkeypatch.setattr(index_mod, "build_index", flaky)
    m = _mgr(synthetic_root, tmp_path)
    m.start_background()
    m._thread.join()
    assert m.state("v99").error
    with pytest.raises(RuntimeError, match="will retry after"):
        m.get()
    assert calls["n"] == 1
    monkeypatch.setattr(IndexManager, "RETRY_AFTER", 0.0)
    idx = m.get()
    assert calls["n"] == 2 and m.state("v99").error is None
    assert idx.stats()["pages"] == 4
    idx.close()


def test_failed_retry_propagates(synthetic_root, tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise OSError("boom")

    monkeypatch.setattr(index_mod, "build_index", boom)
    monkeypatch.setattr(IndexManager, "RETRY_AFTER", 0.0)
    m = _mgr(synthetic_root, tmp_path)
    m.start_background()
    m._thread.join()
    with pytest.raises(RuntimeError, match="failed again on retry.*boom"):
        m.get()


# ---------------- tool errors (ToolError -> is_error) ----------------

@pytest.fixture()
def server_tools(synthetic_root, tmp_path):
    from studio5000_docs_mcp import server

    m = IndexManager(Config(help_root=synthetic_root, db_dir=tmp_path / "db"))
    m.build("v99")
    server.set_manager(m)
    yield server
    m.get().close()
    server._manager = None


def test_tool_unknown_page_raises(server_tools):
    with pytest.raises(ToolError, match="No page 'nope'"):
        server_tools.get_page("nope")
    with pytest.raises(ToolError, match="No page 'nope'"):
        server_tools.get_breadcrumb("nope")


def test_tool_bad_kind_and_version_raise(server_tools):
    with pytest.raises(ToolError, match="kind must be"):
        server_tools.search_help("widget", kind="bogus")
    with pytest.raises(ToolError, match="Unknown version.*Available: v99"):
        server_tools.get_page("ait_1", version="v1")


def test_tool_zero_results_is_normal_text(server_tools):
    assert "No results" in server_tools.search_help("nonexistentterm")
    assert "No page for help id" in server_tools.get_page_by_help_id("999")


def test_tool_no_manager_versions_raises(tmp_path):
    from studio5000_docs_mcp import server

    server.set_manager(IndexManager(Config(help_root=tmp_path / "empty", db_dir=tmp_path / "db")))
    try:
        with pytest.raises(ToolError, match="No Studio 5000 help folders"):
            server.get_page("x")
        assert "No Studio 5000 help folders" in server.list_versions()
    finally:
        server._manager = None


def test_mcp_protocol_is_error_flag(server_tools):
    from mcp import Client

    async def run():
        async with Client(server_tools.mcp) as c:
            bad = await c.call_tool("get_page", {"page_id": "does-not-exist"})
            good = await c.call_tool("get_page", {"page_id": "ait_1"})
            return bad, good

    bad, good = asyncio.run(run())
    assert bad.is_error is True
    assert bad.content[0].text == (
        "Error executing tool get_page: No page 'does-not-exist' in v99. Use search_help to find page_ids.")
    assert good.is_error is False
    assert "Widget Overview" in good.content[0].text
