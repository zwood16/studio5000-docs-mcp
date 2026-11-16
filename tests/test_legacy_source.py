"""LegacySource and an end-to-end build over a synthetic legacy tree."""

import os

from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.discovery import discover
from studio5000_docs_mcp.index import IndexManager
from studio5000_docs_mcp.sources.legacy import LegacySource, is_legacy_root


def test_is_legacy_root(legacy_root, synthetic_root):
    assert is_legacy_root(legacy_root)
    assert not is_legacy_root(synthetic_root)


def test_pages_skip_nav_and_assets(legacy_root):
    src = LegacySource(legacy_root, "v98")
    pages = {p.path: p for p in src.iter_pages()}
    assert sorted(pages) == ["deep.htm", "foo.htm", "foo_1.htm", "ld.htm", "wpt.htm"]
    assert src.page_count() == 5
    assert pages["wpt.htm"].title == "Widget Pulse Timer (WPT)"


def test_toc(legacy_root):
    nodes = LegacySource(legacy_root, "v98").toc()
    by_id = {n.node_id: n for n in nodes}
    assert "t6" not in by_id  # hidden top-level excluded
    assert by_id["t5"].parent_id == "t4"  # nested hidden included
    assert by_id["t2"].title == "Foo & Bar" and by_id["t2"].href == "foo.htm"
    assert by_id["t3"].parent_id is None and by_id["t4"].ord == 0


def test_sections(legacy_root):
    sec = {p.path: p.section for p in LegacySource(legacy_root, "v98").iter_pages()}
    assert sec == {
        "ld.htm": "help/rs5000",
        "foo.htm": "help/rs5000",
        "foo_1.htm": "help/rs5000",  # X_<n>.htm inherits from X.htm
        "wpt.htm": "help/clinset",
        "deep.htm": "help/clinset",  # hidden nested inherits
    }


def test_help_ids_and_keywords(legacy_root):
    src = LegacySource(legacy_root, "v98")
    assert src.help_ids() == [("555", "wpt.htm"), ("556", "deep.htm")]
    assert src.keywords() == {"wpt.htm": ["pulsing", "ticking"]}
    assert src.csv_toc_paths() == {}


def test_fingerprint_changes(legacy_root):
    fp = LegacySource(legacy_root, "v98").fingerprint()
    assert fp == LegacySource(legacy_root, "v98").fingerprint()
    kw = legacy_root / "keywords.csv"
    st = kw.stat()
    os.utime(kw, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert LegacySource(legacy_root, "v98").fingerprint() != fp


def test_discovery_labels_legacy(legacy_root):
    found = discover(Config(help_root=legacy_root))
    assert found["v98"].format == "legacy"


def test_end_to_end_build(legacy_root, tmp_path):
    m = IndexManager(Config(help_root=legacy_root, db_dir=tmp_path / "db"))
    assert m.default is None  # default stays highest oxygen version
    m.build("v98")
    idx = m.get("v98")
    try:
        row = idx.resolve_page("wpt.htm")
        assert row["kind"] == "instruction" and row["mnemonic"] == "WPT"
        assert row["section"] == "help/clinset"
        assert row["breadcrumb"] == "Instruction Set"
        deep = idx.resolve_page("deep.htm")
        assert deep["breadcrumb"] == "Instruction Set > Widget Pulse Timer (WPT)"
        assert deep["section"] == "help/clinset"
        assert idx.search("WPT")[0]["path"] == "wpt.htm"
        assert [r["path"] for r in idx.by_help_id("555")] == ["wpt.htm"]
        assert [r["path"] for r in idx.by_help_id("556")] == ["deep.htm"]
        assert idx.search("pulsing")[0]["path"] == "wpt.htm"  # keyword index
    finally:
        idx.close()
