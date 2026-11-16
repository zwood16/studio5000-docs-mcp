"""Offline tests for help-folder discovery (fake layouts, no real help content)."""

from __future__ import annotations

from pathlib import Path

from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.discovery import (
    VersionInfo,
    default_version,
    discover,
    is_legacy_root,
)


def _legacy(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "toc.json").write_text("{}")
    return root


def _oxygen(root: Path) -> Path:
    (root / "oxygen-webhelp").mkdir(parents=True)
    return root


def _ld(base: Path, n: int) -> Path:
    return base / "Logix Designer" / "ENU" / f"v{n}" / "Bin" / "Help" / "ENU" / "rs5000"


def _help(base: Path, n: int) -> Path:
    return base / "Help" / "ENU" / f"v{n}" / "rs5000"


def _layout(base: Path) -> None:
    _legacy(_help(base, 31))
    _legacy(_ld(base, 36))
    _oxygen(_ld(base, 37))


def test_base_root_finds_all(tmp_path: Path):
    _layout(tmp_path)
    found = discover(Config(help_root=tmp_path))
    assert list(found) == ["v31", "v36", "v37"]
    assert found["v31"].format == "legacy"
    assert found["v31"].root == _help(tmp_path, 31)
    assert found["v36"].format == "legacy"
    assert found["v37"].format == "oxygen"


def test_logix_designer_enu_root_finds_sibling_help(tmp_path: Path):
    _layout(tmp_path)
    found = discover(Config(help_root=tmp_path / "Logix Designer" / "ENU"))
    assert list(found) == ["v31", "v36", "v37"]
    found = discover(Config(help_root=tmp_path / "Logix Designer"))
    assert list(found) == ["v31", "v36", "v37"]


def test_single_rs5000_root(tmp_path: Path):
    root = _oxygen(_ld(tmp_path, 37))
    found = discover(Config(help_root=root))
    assert list(found) == ["v37"]
    assert found["v37"].root == root
    root = _legacy(_help(tmp_path, 32))
    found = discover(Config(help_root=root))
    assert list(found) == ["v32"]
    assert found["v32"].format == "legacy"


def test_logix_designer_tree_wins_duplicates(tmp_path: Path):
    _legacy(_help(tmp_path, 33))
    _legacy(_ld(tmp_path, 33))
    found = discover(Config(help_root=tmp_path))
    assert list(found) == ["v33"]
    assert found["v33"].root == _ld(tmp_path, 33)


def test_old_versions_excluded(tmp_path: Path):
    v30 = tmp_path / "Help" / "ENU" / "v30"
    v30.mkdir(parents=True)
    (v30 / "help.chm").write_bytes(b"")
    _legacy(_help(tmp_path, 29))
    _legacy(_help(tmp_path, 31))
    found = discover(Config(help_root=tmp_path))
    assert list(found) == ["v31"]


def test_format_labels(tmp_path: Path):
    _oxygen(_help(tmp_path, 35))
    _legacy(_help(tmp_path, 34))
    (_help(tmp_path, 33)).mkdir(parents=True)
    found = discover(Config(help_root=tmp_path))
    assert {k: v.format for k, v in found.items()} == {
        "v33": "unknown",
        "v34": "legacy",
        "v35": "oxygen",
    }
    assert is_legacy_root(_help(tmp_path, 34))
    assert not is_legacy_root(_help(tmp_path, 35))
    assert not is_legacy_root(_help(tmp_path, 33))


def test_default_version_picks_oxygen(tmp_path: Path):
    _layout(tmp_path)
    found = discover(Config(help_root=tmp_path))
    assert default_version(found, None) == "v37"
    assert (
        default_version({"v36": VersionInfo("v36", tmp_path, "legacy")}, None) is None
    )
