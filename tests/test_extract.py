from pathlib import Path

import pytest

from studio5000_docs_mcp.extract import (
    SYNONYMS,
    clean_punct,
    expand_identifiers,
    is_stop,
    parse_html,
    query_term,
    query_tokens,
    resolve_ref,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_page.html"


@pytest.fixture(scope="module")
def page():
    return parse_html(FIXTURE.read_text(encoding="utf-8"), "fallback.html")


def test_metadata(page):
    assert page.path == "help/sub/page.html"  # wh-out-relpath wins over the fallback
    assert page.title == "Widget Counter (WCT)"
    assert page.description == "Counts widgets."
    assert page.identifier == "ait_1"
    assert page.toc_id == "ait_1-d1"
    assert (
        page.relation == "help/index.html"
    )  # DC.relation resolved against the page path


def test_fallback_path_without_meta():
    p = parse_html(
        "<html><head><title>T</title></head><body><main><article><p>x</p></article></main></body></html>",
        "help/z.html",
    )
    assert p.path == "help/z.html" and p.relation == "" and p.markdown == "# T\n\nx"


def test_title_heading_prepended(page):
    assert page.markdown.startswith("# Widget Counter (WCT)\n\n")
    assert page.markdown.count("# Widget Counter") == 1  # article h1 not duplicated


def test_subheading_and_note(page):
    assert "\n### Operation\n" in page.markdown
    assert "> **Note:** Be careful here." in page.markdown


def test_links_and_images(page):
    assert "[link text](page:x/y.html)" in page.markdown
    assert page.links == ["x/y.html"]
    assert "![pic](help/sub/graphics/a.png)" in page.markdown


def test_nav_and_script_stripped(page):
    assert "NAVTEXT" not in page.markdown
    assert "SCRIPTTEXT" not in page.markdown
    assert "var x" not in page.markdown


def test_table(page):
    rows = [ln for ln in page.markdown.splitlines() if ln.startswith("|")]
    assert rows[0] == "| Name | Type | Info |"
    assert rows[1] == "| --- | --- | --- |"
    assert "| .A | Spans two |  |" in rows  # colspan: second cell empty, row padded
    assert "| Tall | one \\| pipe | x |" in rows  # pipe escaped
    assert "| Tall | two | y |" in rows  # rowspan repeated on the next row
    assert "| Multi | First para<br>Second para | - item 1<br>- item 2 |" in rows
    assert all(
        not r.startswith("|") or r.count("|") - r.count("\\|") == 4 for r in rows
    )


def test_external_and_anchor_links():
    html = (
        "<html><head><title>T</title></head><body><main><article>"
        '<p><a href="https://example.com/a">ext</a> <a href="#top">anchor</a> '
        '<a href="doc.pdf">pdf</a></p>'
        "</article></main></body></html>"
    )
    p = parse_html(html, "help/a.html")
    assert "[ext](https://example.com/a)" in p.markdown
    assert "page:" not in p.markdown
    assert p.links == []


def test_resolve_ref():
    assert resolve_ref("help/a/b.html", "../c/d.html#frag") == "help/c/d.html"
    assert resolve_ref("help/a/b.html", "mailto:x@y.z") is None
    assert resolve_ref("help/a/b.html", "../../../x.html") is None
    assert resolve_ref("help/a/b.html", "#x") is None


# ---------------- FTS helpers ----------------


def test_clean_punct():
    assert clean_punct("the end. Next-") == "the end Next"
    assert clean_punct("foo_ bar-") == "foo bar"
    assert clean_punct("Timer.DN. and .DN") == "Timer.DN and .DN"
    assert clean_punct("1756-IF16 module.") == "1756-IF16 module"


def test_expand_identifiers():
    t = expand_identifiers("Timer.DN").split()
    assert {"timer", "dn", ".dn"} <= set(t)
    t = expand_identifiers("1756-IF16").split()
    assert {"1756", "if16"} <= set(t)
    assert expand_identifiers(".DN") == "dn"
    assert expand_identifiers("plain words") == ""
    assert {"axis", "cip", "drive"} <= set(expand_identifiers("Axis_CIP_Drive").split())


def test_query_tokens_basic():
    assert query_tokens(".DN") == [".DN"]
    assert query_tokens("1756-IF16") == ["1756-IF16"]
    assert query_tokens("Timer.DN.") == ["Timer.DN"]


@pytest.mark.parametrize(
    "q",
    [
        '"',
        'what "sets" TON',
        "(",
        ")))",
        "AND OR NOT NEAR",
        "NEAR(a b)",
        "a*",
        "^x",
        "col:val",
        "title:TON",
        "",
        "   ",
        "-",
        "' OR 1=1 --",
        "\\",
        "{x}",
        "café 日本語 αβ",
    ],
)
def test_query_tokens_hostile(q):
    toks = query_tokens(q)
    assert isinstance(toks, list)
    for t in toks:
        assert t and not any(c in t for c in "\"()*^:{}\\' ")


def test_query_tokens_hostile_content():
    assert query_tokens("") == []
    assert query_tokens('a*"b') == ["a", "b"]
    assert query_tokens("col:val") == ["col", "val"]
    assert query_tokens("AND OR NOT NEAR") == [
        "AND",
        "OR",
        "NOT",
        "NEAR",
    ]  # quoted later, so harmless
    assert "café" in query_tokens("café au lait")


def test_is_stop():
    assert is_stop("the") and is_stop("The") and is_stop("how")
    assert not is_stop("FOR") and not is_stop("AND")  # all-caps are mnemonics
    assert not is_stop("timer")
    assert is_stop("I")  # single capital letter is still a stop word


def test_question_stopwords_dropped_but_caps_kept():
    for w in (
        "how",
        "do",
        "does",
        "what",
        "which",
        "when",
        "where",
        "why",
        "I",
        "the",
        "a",
        "an",
        "to",
        "of",
        "with",
        "between",
        "difference",
        "use",
        "using",
        "my",
        "Using",
    ):
        assert is_stop(w), w
    for w in ("FOR", "AND", "OR", "TO", "USE"):
        assert not is_stop(w), w


def test_query_term_hyphen_split():
    orig, exp = query_term("program-scoped")
    assert orig == '("program-scoped" OR ("program" AND "scoped"))'
    assert exp == orig  # hyphenated tokens get no synonym expansion
    assert (
        query_term("false-to-true")[0] == '("false-to-true" OR ("false" AND "true"))'
    )  # stopword part dropped
    assert query_term("1756-IF16") == (
        '"1756-IF16"',
        '"1756-IF16"',
    )  # catalog id stays whole
    assert query_term("Timer.DN") == ('"Timer.DN"', '"Timer.DN"')


def test_query_term_synonyms():
    orig, exp = query_term("create")
    assert orig == '"create"'
    assert exp == '("create" OR "define" OR "new")'
    assert (
        query_term("Create")[1] == '("Create" OR "define" OR "new")'
    )  # FTS is case-insensitive
    assert query_term("FOR") == ('"FOR"', '"FOR"')  # ALL-CAPS never expanded
    assert query_term("timer") == ('"timer"', '"timer"')
    assert all(
        v and all(isinstance(x, str) and '"' not in x for x in v)
        for v in SYNONYMS.values()
    )
    assert all(k == k.lower() for k in SYNONYMS)


@pytest.mark.parametrize(
    "q",
    [
        '"',
        'what "sets" TON',
        "(",
        ")))",
        "AND OR NOT NEAR",
        "NEAR(a b)",
        "a*",
        "^x",
        "col:val",
        "title:TON",
        "",
        "   ",
        "-",
        "' OR 1=1 --",
        "\\",
        "{x}",
        "café 日本語 αβ",
        "a-b-c-",
        "create-new",
        "-x-y",
    ],
)
def test_query_term_hostile(q):
    for t in query_tokens(q):
        orig, exp = query_term(t)
        for frag in (orig, exp):
            assert frag.count('"') % 2 == 0
            assert not any(c in frag.replace('"', "") for c in "*^:{}\\'")
