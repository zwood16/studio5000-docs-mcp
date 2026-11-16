"""Version-qualified page ids with two versions: oxygen v37 (default), legacy v33."""

import re
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
    v33 = base / "v33" / "Bin" / "Help" / "ENU" / "rs5000"
    shutil.copytree(legacy_root, v33)
    paras = "".join(
        f'<p>Paragraph {i} about widgets, see <a href="wpt.htm">the timer</a> and '
        f'<a href="deep.htm">deep page</a> for more filler text here.</p>'
        for i in range(60)
    )
    (v33 / "long.htm").write_text(
        "<html><head><title> Long Widget Page </title></head><body>"
        f'<div id="content_section"><h1>Long Widget Page</h1>{paras}</div>'
        "</body></html>",
        encoding="utf-8",
    )
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


def test_pagination_round_trips_with_qualified_links(two):
    stored = two._manager.get("v33").resolve_page("long.htm")["markdown"]
    assert len(stored) > 3000 and "](page:wpt.htm)" in stored
    got, offset, pages = "", 0, 0
    while True:
        out = two.get_page("v33:long.htm", offset=offset, max_chars=500)
        pages += 1
        body = re.split(r"chars: \d+\n", out, maxsplit=1)[1]
        m = re.search(r"\n\n\[Truncated: .*?offset=(\d+) ", body)
        body = body.split("\n\n---\nOutgoing links:")[0]
        body = re.split(r"\n\n\[(?:Truncated|End of page)", body)[0]
        got += body.replace("](page:v33:", "](page:")
        if not m:
            break
        offset = int(m.group(1))
    assert pages > 5
    assert got == stored


def test_instructions_text():
    assert server.INSTRUCTIONS.startswith(
        "Studio 5000 Logix Designer help (Rockwell), one searchable index per"
        " installed version (v31+)."
    )
    assert "\nVERSIONS\n- Help content differs between Logix Designer versions" in (
        server.INSTRUCTIONS
    )
    assert server.INSTRUCTIONS.index("VERSIONS") < server.INSTRUCTIONS.index("TIPS")


@pytest.fixture()
def unbuilt(synthetic_root, legacy_root, tmp_path):
    base = tmp_path / "Studio 5000" / "Logix Designer" / "ENU"
    shutil.copytree(synthetic_root, base / "v37" / "Bin" / "Help" / "ENU" / "rs5000")
    for v in ("v33", "v34"):
        shutil.copytree(legacy_root, base / v / "Bin" / "Help" / "ENU" / "rs5000")
    return base, tmp_path / "db"


def test_list_versions_state_and_identical(unbuilt):
    base, db = unbuilt
    m = IndexManager(Config(help_root=base, db_dir=db))
    server.set_manager(m)
    try:
        assert m._signatures == {}  # lazy: nothing computed at startup
        out = server.list_versions().splitlines()
        assert len(out) == 3
        assert "index=not loaded (opened from cache or built on first use)" in out[0]
        assert "same help content" not in out[0]
        assert "v34" in out[1] and "same help content as v33" in out[1]
        assert "v37 (default)" in out[2] and "same help content" not in out[2]
        m.build("v37")
        assert "index=ready, 4 pages" in server.list_versions().splitlines()[2]
        m.get("v37").close()
    finally:
        server._manager = None


def test_content_signature_differs(synthetic_root, legacy_root):
    from studio5000_docs_mcp.sources.legacy import LegacySource
    from studio5000_docs_mcp.sources.oxygen import OxygenSource

    a = LegacySource(legacy_root, "v1").content_signature()
    assert a == LegacySource(legacy_root, "v1").content_signature()
    (legacy_root / "toc.json").write_text("[]", encoding="utf-8")
    assert LegacySource(legacy_root, "v1").content_signature() != a
    assert OxygenSource(synthetic_root, "v1").content_signature()[0] == "oxygen"


def test_cli_build_only_all(unbuilt, monkeypatch, capsys):
    base, db = unbuilt
    monkeypatch.setenv("S5K_DB_DIR", str(db))
    monkeypatch.delenv("S5K_HELP_ROOT", raising=False)
    with pytest.raises(SystemExit) as e:
        server.main(["--root", str(base), "--build-only", "--all"])
    assert e.value.code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [ln.split()[0] for ln in lines] == ["v33", "v34", "v37"]
    assert lines[0].split()[1:4] == ["legacy", "5", "yes"]
    assert lines[2].split()[1] == "oxygen" and lines[2].endswith("v37.sqlite")
    with pytest.raises(SystemExit) as e:
        server.main(["--root", str(base), "--build-only", "--all"])
    assert capsys.readouterr().out.splitlines()[0].split()[3] == "no"
    server._manager = None


def test_cli_build_all_failure_and_flag_check(unbuilt, monkeypatch, capsys):
    base, db = unbuilt
    monkeypatch.setenv("S5K_DB_DIR", str(db))
    orig = IndexManager.build

    def flaky(self, version, force=None):
        if version == "v34":
            raise ValueError("boom")
        return orig(self, version, force)

    monkeypatch.setattr(IndexManager, "build", flaky)
    with pytest.raises(SystemExit) as e:
        server.main(["--root", str(base), "--build-only", "--all"])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "v34  legacy  ERROR: ValueError: boom" in out and "v37  oxygen" in out
    with pytest.raises(SystemExit) as e:
        server.main(["--root", str(base), "--all"])
    assert e.value.code == 2
    server._manager = None
