import json
import os
from pathlib import Path

import pytest

from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.discovery import default_version, discover
from studio5000_docs_mcp.index import IndexManager


def _config(db_dir: Path | None = None) -> Config:
    env = os.environ.get("S5K_HELP_ROOT")
    if not env:
        pytest.skip(
            "S5K_HELP_ROOT not set (integration tests need a real help install)"
        )
    return Config(help_root=Path(env), db_dir=db_dir)


@pytest.fixture(scope="session")
def manager(tmp_path_factory):
    cfg = _config(tmp_path_factory.mktemp("s5k-db"))
    versions = discover(cfg)
    if not default_version(versions, None):
        pytest.skip("Studio 5000 help root not found (set S5K_HELP_ROOT)")
    m = IndexManager(cfg)
    m.build(m.default)
    return m


@pytest.fixture(scope="session")
def index(manager):
    return manager.get()


@pytest.fixture(scope="session")
def legacy_index(manager):
    """The v36 (legacy format) index from the same S5K_HELP_ROOT."""
    if "v36" not in manager.versions:
        pytest.skip("v36 help not found under S5K_HELP_ROOT")
    return manager.get("v36")


@pytest.fixture(scope="session")
def tools(manager):
    from studio5000_docs_mcp import server

    server.set_manager(manager)
    return server


# ---------------- synthetic offline help tree ----------------


def _page(path, title, ident, rel="", body="", toc_id=""):
    rel_meta = f'<meta name="DC.relation" content="{rel}">' if rel else ""
    return (
        f"<html><head><title>{title}</title>"
        f'<meta name="DC.identifier" content="{ident}">'
        f'<meta name="wh-toc-id" content="{toc_id}">'
        f'<meta name="wh-out-relpath" content="{path}">{rel_meta}'
        f"</head><body><main><article><h1>{title}</h1>{body}</article></main>"
        "</body></html>"
    )


@pytest.fixture()
def synthetic_root(tmp_path):
    """A tiny made-up Oxygen help tree.

    rs5000-style root with nav-links and map files."""
    root = tmp_path / "v99" / "Bin" / "Help" / "ENU" / "rs5000"
    pages = {
        "help/a.html": _page(
            "help/a.html",
            "Widget Overview",
            "ait_1",
            toc_id="ait_1-d1",
            body='<p>The gadget frobnicator links to <a href="clinset/wct.html">'
            'counter</a> and <a href="missing.html">nowhere</a>.</p>',
        ),
        "help/clinset/wct.html": _page(
            "help/clinset/wct.html",
            "Widget Counter (WCT)",
            "ait_2",
            rel="../a.html",
            body="<p>Counts widgets with the .CNT member of Timer.DN style tags.</p>",
        ),
        "help/orphan.html": _page(
            "help/orphan.html",
            "Orphan Page",
            "ait_3",
            rel="a.html",
            body="<p>Zebra stripes.</p>",
        ),
        "help/csvonly.html": _page(
            "help/csvonly.html", "Csv Only Page", "ait_4", body="<p>Quokka.</p>"
        ),
    }
    for rel, html in pages.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(html, encoding="utf-8")
    nav = root / "oxygen-webhelp" / "app" / "nav-links" / "json"
    nav.mkdir(parents=True)
    (nav / "nav-links.js").write_text(
        'define({"title":"<span class=\\"title\\">Synthetic Help</span>",'
        '"topics":[{"title":"Widget Overview",'
        '"href":"help\\/a.html","attributes":{"data-id":"ait_1"},"menu":{"hasChildren":false},'
        '"tocID":"ait_1-d1","next":"ait_1-d1"}]});',
        encoding="utf-8",
    )
    (nav / "ait_1-d1.js").write_text(
        'oxy_webhelp_navlinks = {"ait_1-d1":{"topics":[{"title":"Widget Counter (WCT)",'
        '"href":"help\\/clinset\\/wct.html","attributes":{"data-id":"ait_2"},"menu":{"hasChildren":false},'
        '"tocID":"ait_2-d2","next":"ait_2-d2"}]}};',
        encoding="utf-8",
    )
    (root / "context-help-map.xml").write_text(
        '<map><appContext helpID="123" path="help/clinset/wct.html"/>'
        '<appContext helpID="" path="help/a.html"/></map>',
        encoding="utf-8",
    )
    (root / "rs5000.csv").write_text(
        "456,help/a.html,contents-ditamap/Top-Section/Sub_Section/a.html\n"
        "789,help/csvonly.html,contents-ditamap/Csv-Section/Deeper/csvonly.html\n",
        encoding="utf-8",
    )
    (root / "index.xml").write_text(
        '<index xmlns="http://www.oxygenxml.com/ns/webhelp/index"><term name="gadgets">'
        '<term name="frobnicate"><target>help/a.html</target></term></term></index>',
        encoding="utf-8",
    )
    return root


# ---------------- synthetic offline legacy help tree ----------------

_LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_page.htm"


def _legacy_html(title: str, body_word: str = "") -> str:
    html = _LEGACY_FIXTURE.read_text(encoding="utf-8")
    html = html.replace("Widget Pulse Timer (WPT)", title)
    html = html.replace('href="1003.htm"', 'href="wpt.htm"')
    return html.replace(
        "The WPT instruction emits a pulse",
        f"{body_word} The instruction emits a pulse",
    )


@pytest.fixture()
def legacy_root(tmp_path):
    """A tiny made-up legacy (v31-v36 style) help tree under a v98 folder."""
    root = tmp_path / "v98" / "Bin" / "Help" / "ENU" / "rs5000"
    root.mkdir(parents=True)
    pages = {
        "ld.htm": _legacy_html("Widget Designer Overview", "Platypus."),
        "wpt.htm": _legacy_html("Widget Pulse Timer (WPT)", "Wombat."),
        "deep.htm": _legacy_html("Deep Hidden Widget Page", "Narwhal."),
        "foo.htm": _legacy_html("Foo Page", "Axolotl."),
        "foo_1.htm": _legacy_html("Foo Page", "Axolotl."),
        "index.htm": "<html><head><title>Nav</title></head><body>nav</body></html>",
        "indexpage.htm": "<html><body>nav</body></html>",
        "search_results.htm": "<html><body>nav</body></html>",
        "heading.htm": "<html><body>nav</body></html>",
    }
    for name, html in pages.items():
        (root / name).write_text(html, encoding="utf-8")
    (root / "mft").mkdir()
    (root / "mft" / "ignored.htm").write_text("<html></html>", encoding="utf-8")
    toc = [
        {
            "id": 1,
            "o": 1,
            "text": "Logix Designer",
            "url": "ld.htm",
            "node": "0",
            "children": [
                {
                    "id": 2,
                    "o": 2,
                    "text": "Foo &amp; Bar",
                    "url": "foo.htm#x",
                    "leaf": True,
                }
            ],
        },
        {
            "id": 3,
            "o": 3,
            "text": "Instruction Set",
            "url": "",
            "node": "1",
            "children": [
                {
                    "id": 4,
                    "o": 4,
                    "text": "Widget Pulse Timer (WPT)",
                    "url": "wpt.htm",
                    "children": [
                        {
                            "id": 5,
                            "o": 5,
                            "text": "Deep Hidden Widget Page",
                            "url": "deep.htm",
                            "hidden": True,
                            "leaf": True,
                        }
                    ],
                }
            ],
        },
        {"id": 6, "o": 6, "text": "Foo Page", "url": "foo_1.htm", "hidden": True},
    ]
    (root / "toc.json").write_text(json.dumps(toc), encoding="utf-8-sig")
    (root / "rs5000.csv").write_text(
        "555,wpt.htm\n\n556,deep.htm#frag\n", encoding="utf-8"
    )
    (root / "keywords.csv").write_text(
        '"Title","URL","Keyword","Rank"\n'
        '"Widget Pulse Timer","wpt.htm","pulsing","1"\n'
        '"Widget Pulse Timer","wpt.htm","pulsing","2"\n'
        '"Widget Pulse Timer","wpt.htm","ticking","3"\n',
        encoding="utf-8",
    )
    return root
