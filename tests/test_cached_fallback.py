"""Offline tests: Windows probe fallback, rediscovery, cached-index fallback."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from studio5000_docs_mcp import discovery
from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.index import IndexManager


def test_windows_probe_bases_fall_back_without_env(monkeypatch):
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    bases = [str(b) for b in discovery._windows_probe_bases()]
    assert len(bases) == 2
    assert "Program Files (x86)" in bases[0]
    assert "Program Files" in bases[1] and "(x86)" not in bases[1]
    assert all(b.endswith("Studio 5000") for b in bases)


def test_windows_probe_bases_dedupe_case_insensitive(monkeypatch):
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles", r"c:\program files")
    assert len(discovery._windows_probe_bases()) == 1


def test_rediscovery_after_help_root_appears(synthetic_root, tmp_path, monkeypatch):
    late = tmp_path / "late"
    cfg = Config(help_root=late, db_dir=tmp_path / "db")
    m = IndexManager(cfg)
    with pytest.raises(ValueError, match="No Studio 5000 help folders found"):
        m.get()
    # Interval not elapsed yet: still nothing even though the tree now exists.
    shutil.copytree(synthetic_root.parents[3], late / "v99")
    with pytest.raises(ValueError, match="No Studio 5000 help folders found"):
        m.get()
    monkeypatch.setattr(IndexManager, "REDISCOVER_AFTER", 0.0)
    assert m.get(wait=60).stats()["pages"] > 0
    assert m.default == "v99"
    assert not m.versions["v99"].cached


def _build(root: Path, db: Path) -> IndexManager:
    m = IndexManager(Config(help_root=root, db_dir=db))
    m.build(m.default)
    return m


def test_cached_fallback_and_restore(synthetic_root, tmp_path, monkeypatch):
    db = tmp_path / "db"
    m0 = _build(synthetic_root, db)
    pages = m0.get().stats()["pages"]
    m0.get().close()
    dbp = db / "v99.sqlite"
    mtime = dbp.stat().st_mtime_ns

    gone = synthetic_root.parents[3].with_name("hidden")
    synthetic_root.parents[3].rename(gone)
    m = IndexManager(Config(help_root=synthetic_root, db_dir=db))
    assert m.versions["v99"].cached is True
    assert m.content_signature("v99") is None
    idx = m.get("v99")
    assert idx.stats()["pages"] == pages
    assert dbp.stat().st_mtime_ns == mtime
    # force is ignored while unreachable
    assert m.build("v99", force=True) is idx
    assert dbp.stat().st_mtime_ns == mtime

    gone.rename(synthetic_root.parents[3])
    monkeypatch.setattr(IndexManager, "REDISCOVER_AFTER", 0.0)
    m.refresh()
    assert m.versions["v99"].cached is False
    assert m.get("v99").stats()["pages"] == pages


def test_cached_db_from_other_root_not_picked_up(synthetic_root, tmp_path):
    db = tmp_path / "db"
    _build(synthetic_root, db).get().close()
    other = tmp_path / "elsewhere"  # does not exist, not an ancestor of the DB root
    m = IndexManager(Config(help_root=other, db_dir=db))
    assert m.versions == {}
    if os.name != "nt":  # no search area when help_root is unset
        assert IndexManager(Config(help_root=None, db_dir=db)).versions == {}


def test_build_unreachable_without_db_raises(synthetic_root, tmp_path):
    cfg = Config(help_root=synthetic_root, db_dir=tmp_path / "db")
    m = IndexManager(cfg)
    gone = synthetic_root.parents[3].with_name("hidden")
    synthetic_root.parents[3].rename(gone)
    with pytest.raises(RuntimeError, match="unreachable and no usable cached index"):
        m.build("v99")
