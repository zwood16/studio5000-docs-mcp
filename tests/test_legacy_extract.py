from pathlib import Path

import pytest

from studio5000_docs_mcp.extract import parse_legacy_html

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_page.htm"


@pytest.fixture(scope="module")
def page():
    return parse_legacy_html(FIXTURE.read_text(encoding="utf-8"), "1004.htm")


def test_title_and_heading(page):
    assert page.title == "Widget Pulse Timer (WPT)"
    assert page.markdown.startswith("# Widget Pulse Timer (WPT)\n")
    assert page.path == "1004.htm"
    assert page.identifier == "" and page.toc_id == "" and page.relation == ""


def test_chrome_removed(page):
    md = page.markdown
    for bad in (
        "Book Contents",
        "Book Index",
        "Search the fake help",
        "Go find",
        "TOC column text",
        "Footer text",
        "Below nav text",
        "pulse widget keyword",
    ):
        assert bad not in md


def test_note(page):
    assert "> **IMPORTANT** Do not feed the widget after midnight." in page.markdown
    assert "> - Second point" in page.markdown


def test_data_table(page):
    assert (
        "| Operand | Type |\n| --- | --- |\n| Timer | WIDGET_TIMER |" in page.markdown
    )
    assert "IMPORTANT** Operand" not in page.markdown


def test_subheading_code_image(page):
    assert "### Operands" in page.markdown
    assert "`WPT(my_timer);`" in page.markdown
    assert "![Widget diagram](3.gif)" in page.markdown


def test_links(page):
    assert "[Widget Reset (WRS)](page:1003.htm)" in page.markdown
    assert page.links == ["1002.htm", "1003.htm"]


def test_crumb_trail(page):
    assert page.crumb_trail == [
        ("Widget Instructions", "1001.htm"),
        ("Pulse Instructions", "1002.htm"),
    ]


def test_stacked_and_spacer_notes(page):
    md = page.markdown
    assert "> **IMPORTANT** Stacked note one.\n\n> **EXAMPLE** Stacked note two." in md
    assert (
        "> **IMPORTANT** Spacer note body.\n\n> **SAFETY ADVISORY** Advisory body" in md
    )
    assert "advisory.png" not in md
    assert "tableheadingright" not in md and "| **IMPORTANT**" not in md


def test_tip_heading_and_icon_notes(page):
    assert "> **TIP** Heading-style tip body." in page.markdown
    assert "> **NOTE** Icon note body." in page.markdown


def test_icon_legend_stays_table(page):
    assert "| ![](save.png) | Saves the widget. |" not in page.markdown  # alt kept
    assert "![TOOLBAR_SAVE_ICON](save.png) | Saves the widget. |" in page.markdown
    assert "![1](two.png) | Resets the widget. |" in page.markdown
    assert "> **TOOLBAR" not in page.markdown
