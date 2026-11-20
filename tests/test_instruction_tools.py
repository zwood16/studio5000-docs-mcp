"""get_instruction aliases/paging and get_image over a synthetic tree."""

import base64

import pytest
from conftest import _page
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from studio5000_docs_mcp import server
from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.index import MNEMONIC_RENAMES_V36, IndexManager

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def tools(synthetic_root, tmp_path):
    pages = {
        "help/clinset/le.html": _page(
            "help/clinset/le.html",
            "Less Than or Equal To (LE)",
            "ait_10",
            body="<p>Made-up page about LE.</p>",
        ),
        "help/clinset/mov.html": _page(
            "help/clinset/mov.html",
            "Move (MOV)",
            "ait_11",
            body="<p>Made-up page about MOV.</p>",
        ),
        "help/clinset/long.html": _page(
            "help/clinset/long.html",
            "Long Thing (LNG)",
            "ait_12",
            body="".join(f"<p>Filler {i} " + "x" * 80 + "</p>" for i in range(60)),
        ),
        "help/clinset/sqr.html": _page(
            "help/clinset/sqr.html",
            "Square Root (SQR/SQRT)",
            "ait_13",
            body="<p>Made-up square root.</p>",
        ),
    }
    for rel, html in pages.items():
        (synthetic_root / rel).write_text(html, encoding="utf-8")
    gdir = synthetic_root / "help" / "clinset" / "graphics"
    gdir.mkdir()
    (gdir / "Pic.png").write_bytes(PNG)
    (gdir / "big.png").write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    (gdir / "d.svg").write_text("<svg/>", encoding="utf-8")
    (gdir / "note.txt").write_text("x", encoding="utf-8")
    (synthetic_root.parent.parent.parent.parent / "secret.png").write_bytes(PNG)
    m = IndexManager(Config(help_root=synthetic_root, db_dir=tmp_path / "db"))
    m.build("v99")
    server.set_manager(m)
    yield server
    m.get().close()
    server._manager = None


def test_rename_map_has_no_self_or_chained_entries():
    assert len(MNEMONIC_RENAMES_V36) == 16
    assert not set(MNEMONIC_RENAMES_V36) & set(MNEMONIC_RENAMES_V36.values())


def test_slash_mnemonics_indexed(tools):
    assert "title: Square Root" in tools.get_instruction("SQR")
    assert "title: Square Root" in tools.get_instruction("SQRT")
    assert "renamed" not in tools.get_instruction("SQR")


def test_alias_old_to_new(tools):
    out = tools.get_instruction("LEQ")
    assert out.startswith(
        "Note: LEQ was renamed LE in Logix Designer v36; showing LE from v99."
    )
    assert "title: Less Than or Equal To (LE)" in out


def test_alias_new_to_old(tools):
    out = tools.get_instruction("move")
    assert out.startswith("Note: MOVE was named MOV before Logix Designer v36")
    assert "title: Move (MOV)" in out


def test_exact_match_has_no_note_and_unknown_falls_back(tools):
    assert not tools.get_instruction("LE").startswith("Note:")
    out = tools.get_instruction("EQU")  # alias EQ not present either
    assert not out.startswith("Note:")


def test_get_instruction_offset(tools):
    first = tools.get_instruction("LNG", max_chars=600)
    assert "[Truncated: showing chars 0-600" in first
    nxt = tools.get_instruction("LNG", offset=600, max_chars=600)
    assert "[Truncated: showing chars 600-1200" in nxt
    assert "Outgoing links" not in nxt


def test_get_image_ok(tools):
    img = tools.get_image("help/clinset/graphics/Pic.png")
    assert isinstance(img, Image) and img.data == PNG
    assert img._mime_type == "image/png"
    # version-qualified and case-insensitive
    img = tools.get_image("v99:help/clinset/graphics/pic.PNG")
    assert img.data == PNG
    assert tools.get_image("help/clinset/graphics/d.svg") == "<svg/>"


def test_get_image_rejections(tools):
    with pytest.raises(ToolError, match="outside"):
        tools.get_image("../../../../../secret.png")
    with pytest.raises(ToolError, match="outside"):
        tools.get_image("help/../../x.png")
    with pytest.raises(ToolError, match="relative"):
        tools.get_image("/etc/passwd.png")
    with pytest.raises(ToolError, match="Unsupported"):
        tools.get_image("help/clinset/graphics/note.txt")
    with pytest.raises(ToolError, match="No image"):
        tools.get_image("help/clinset/graphics/missing.png")
    with pytest.raises(ToolError, match="limit"):
        tools.get_image("help/clinset/graphics/big.png")


def test_get_image_help_folder_unreachable(tools):
    import shutil

    shutil.rmtree(tools._manager.versions["v99"].root)
    with pytest.raises(ToolError, match="not reachable"):
        tools.get_image("help/clinset/graphics/Pic.png")


def test_get_image_through_mcp(tools):
    import asyncio

    from mcp import Client

    async def run():
        async with Client(tools.mcp) as c:
            ok = await c.call_tool(
                "get_image", {"path": "help/clinset/graphics/Pic.png"}
            )
            svg = await c.call_tool(
                "get_image", {"path": "help/clinset/graphics/d.svg"}
            )
            bad = await c.call_tool("get_image", {"path": "../x.png"})
            return ok, svg, bad

    ok, svg, bad = asyncio.run(run())
    assert ok.is_error is False and ok.content[0].type == "image"
    assert ok.content[0].mime_type == "image/png"
    assert base64.b64decode(ok.content[0].data) == PNG
    assert svg.content[0].text == "<svg/>"
    assert bad.is_error is True
