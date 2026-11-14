import json
import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from studio5000_docs_mcp.extract import expand_identifiers, query_tokens
from studio5000_docs_mcp.index import extract_mnemonics

TON = "help/clinset/timer-counter/timer-on-delay--ton-.html"


@pytest.mark.integration
def test_page_count(index):
    n = index.stats()["pages"]
    assert abs(n - 5493) / 5493 <= 0.02, n


@pytest.mark.integration
def test_titles_and_bodies_nonempty(index):
    c = index.conn
    assert c.execute("SELECT count(*) FROM pages WHERE title=''").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM pages WHERE markdown=''").fetchone()[0] == 0
    # pages whose body is only the title heading are rare
    only_title = c.execute("SELECT count(*) FROM pages WHERE chars <= length(title) + 4").fetchone()[0]
    assert only_title < 0.01 * index.stats()["pages"], only_title


@pytest.mark.integration
def test_ton_markdown_has_timer_table(index):
    md = index.resolve_page(TON)["markdown"]
    assert re.search(r"^\| --- ", md, re.M)
    for member in (".DN", ".PRE", ".ACC"):
        assert re.search(rf"^\| {re.escape(member)} \|", md, re.M), member
    assert "| 4 | 34 |" in md or "| .PRE < 0 | 4 | 34 |" in md


@pytest.mark.integration
def test_resolve_by_id_and_path(index):
    by_path = index.resolve_page(TON)
    assert by_path["id"] == "ait_455128"
    assert index.resolve_page("ait_455128")["path"] == TON
    assert index.resolve_page("/" + TON)["id"] == "ait_455128"


@pytest.mark.integration
def test_help_id(index, tools):
    rows = index.by_help_id("131173")
    assert rows
    assert "131173" in tools.get_page_by_help_id("131173") or "page_id" in tools.get_page_by_help_id("131173")


@pytest.mark.integration
def test_breadcrumbs(index):
    s = index.stats()
    assert s["with_breadcrumb"] / s["pages"] > 0.95


@pytest.mark.integration
def test_instruction_lookup(index, tools):
    assert index.instruction("TON")[0]["path"] == TON
    ssv = index.instruction("SSV")
    assert ssv and all(r["path"].startswith("help/clinset/") for r in ssv)
    assert "Timer On Delay" in tools.get_instruction("TON")
    assert "Set System Value" in tools.get_instruction("SSV")


@pytest.mark.integration
def test_links_rewritten(index):
    md = index.resolve_page(TON)["markdown"]
    targets = re.findall(r"\]\(page:([^)]+)\)", md)
    assert targets
    assert all(index.resolve_page(t) for t in targets)
    assert index.titles_for(json.loads(index.resolve_page(TON)["links"]))


def test_mnemonic_extraction():
    assert extract_mnemonics("Timer On Delay (TON)") == ["TON"]
    assert extract_mnemonics("Get System Value (GSV) and Set System Value (SSV)") == ["GSV", "SSV"]
    assert extract_mnemonics("Options command") == []


@pytest.mark.integration
@pytest.mark.parametrize("q", [
    ".DN", "1756-IF16", 'what "sets" TON .DN', "AND OR NOT", "NEAR(", '"', "a*", "-", "(", ")))", "col:val", "^x", "", "   ",
    "' OR 1=1 --", "title:TON", "TON*", "\\", "foo AND", "NOT", "{x}", "12/34",
])
def test_search_never_errors(index, tools, q):
    index.search(q)
    assert isinstance(tools.search_help(q), str)


@pytest.mark.integration
def test_dot_dn_and_catalog_numbers(index):
    assert index.search(".DN")
    res = index.search("1756-IF16")
    assert res and "1756-IF16" in res[0]["title"]


def test_query_tokens_preserve_identifiers():
    assert query_tokens(".DN") == [".DN"]
    assert query_tokens("1756-IF16") == ["1756-IF16"]
    assert query_tokens("Timer.DN.") == ["Timer.DN"]
    assert ".dn" in expand_identifiers("Timer.DN") and "dn" in expand_identifiers("Timer.DN")


@pytest.mark.integration
def test_get_page_pagination(index, tools):
    big = index.conn.execute("SELECT id FROM pages ORDER BY chars DESC LIMIT 1").fetchone()[0]
    out = tools.get_page(big, max_chars=1000)
    m = re.search(r"Truncated: showing chars 0-(\d+) of \d+\. Call get_page again with offset=(\d+)", out)
    assert m and m.group(1) == m.group(2)
    out2 = tools.get_page(big, offset=int(m.group(2)), max_chars=1000)
    assert "offset=" in out2 or "End of page" in out2


@pytest.mark.integration
def test_other_tools(tools):
    assert "v37" in tools.list_versions()
    assert "pages:" in tools.get_help_statistics()
    top = tools.browse_toc()
    assert "node_id" in top
    nid = re.search(r"node_id: (\S+)", top).group(1)
    assert isinstance(tools.browse_toc(nid), str)
    assert "ait_455128" in tools.get_breadcrumb("ait_455128") or "Timer" in tools.get_breadcrumb("ait_455128")
    with pytest.raises(ToolError, match="No page 'nope'"):
        tools.get_page("nope")
    with pytest.raises(ToolError, match="kind must be"):
        tools.search_help("TON", kind="bogus")
