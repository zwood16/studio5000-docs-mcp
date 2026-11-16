"""Version-qualified page ids with two versions: oxygen v37 (default), legacy v33."""

import shutil

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from studio5000_docs_mcp import server
from studio5000_docs_mcp.config import Config, normalize_version
from studio5000_docs_mcp.index import IndexManager


@pytest.fixture()
def two(synthetic_root, legacy_root, tmp_path):
    base = tmp_path / "Studio 5000" / "Logix Designer" / "ENU"
    shutil.copytree(synthetic_root, base / "v37" / "Bin" / "Help" / "ENU" / "rs5000")
    shutil.copytree(legacy_root, base / "v33" / "Bin" / "Help" / "ENU" / "rs5000")
    m = IndexManager(Config(help_root=base, db_dir=tmp_path / "db"))
    assert m.default == "v37" and set(m.versions) == {"v33", "v37"}
    m.build("v37")
    m.build("v33")
    server.set_manager(m)
    yield server
    for v in ("v33", "v37"):
        m.get(v).close()
    server._manager = None


@pytest.mark.parametrize(
    "raw,want",
    [
        ("v37", "v37"),
        ("V37", "v37"),
        ("37", "v37"),
        (" 37 ", "v37"),
        ("37.00", "v37"),
        ("v33.01", "v33"),
        ("33.011", "v33"),
        ("", None),
        (None, None),
        ("   ", None),
        ("abc", "abc"),
        ("Latest", "latest"),
    ],
)
def test_normalize_version(raw, want):
    assert normalize_version(raw) == want


def test_default_ids_unprefixed(two):
    out = two.search_help("zebra")
    assert "page_id: ait_3" in out and "not the default" not in out
    page = two.get_page("ait_1")
    assert "page_id: ait_1" in page and "(page:ait_2)" in page
    assert "version: v37   chars" in page


def test_search_non_default_prefixed(two):
    out = two.search_help("WPT", version="v33")
    assert "in v33 (not the default v37)" in out
    assert "page_id: v33:wpt.htm" in out


def test_get_page_prefixed_without_version(two):
    out = two.get_page("v33:wpt.htm")
    assert "page_id: v33:wpt.htm" in out
    assert "title: Widget Pulse Timer (WPT)" in out
    assert "version: v33 (not the default v37)   chars" in out
    assert two.get_page("V33:wpt.htm") == out  # prefix is case-insensitive


def test_prefixed_with_version_checks(two):
    with pytest.raises(ToolError, match="is from v33, but version='v37'"):
        two.get_page("v33:wpt.htm", version="v37")
    with pytest.raises(ToolError, match="is from v33"):
        two.get_breadcrumb("v33:wpt.htm", version="37")
    with pytest.raises(ToolError, match="is from v33"):
        two.browse_toc("v33:t3", version="v37")
    assert "title: Widget Pulse Timer" in two.get_page("v33:wpt.htm", version="v33")
    assert "title: Widget Pulse Timer" in two.get_page("v33:wpt.htm", version="33.01")


def test_links_prefixed(two):
    out = two.get_page("v33:ld.htm")
    assert "](page:v33:wpt.htm)" in out
    assert "(page_id: v33:wpt.htm)" in out  # Outgoing links list
    assert "](page:wpt.htm)" not in out


def test_instruction_and_help_id_prefixed(two):
    assert "page_id: v33:wpt.htm" in two.get_instruction("WPT", version="v33")
    assert "page_id: v33:wpt.htm" in two.get_page_by_help_id("555", version="33.01")
    assert "page_id: ait_2" in two.get_instruction("WCT")


def test_browse_and_breadcrumb_round_trip(two):
    top = two.browse_toc(version="v33")
    assert "node_id: v33:t3" in top and "(not the default v37)" in top
    kids = two.browse_toc("v33:t3")
    assert "node_id: v33:t4  page_id: v33:wpt.htm" in kids
    deeper = two.browse_toc("v33:t4")
    assert "page_id: v33:deep.htm" in deeper
    crumb = two.get_breadcrumb("v33:deep.htm")
    assert "(page_id: v33:deep.htm)" in crumb
    assert "> Widget Pulse Timer (WPT)  (page_id: v33:wpt.htm)" in crumb
    assert two.get_breadcrumb("v33:wpt.htm") != two.get_breadcrumb("ait_2")


def test_version_33_01_search(two):
    assert "in v33 (not the default v37)" in two.search_help("WPT", version="33.01")
    with pytest.raises(ToolError, match="Unknown version"):
        two.search_help("WPT", version="latest")
