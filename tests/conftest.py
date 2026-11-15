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
