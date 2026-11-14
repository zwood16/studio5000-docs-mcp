import os
from pathlib import Path

import pytest

from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.discovery import default_version, discover
from studio5000_docs_mcp.index import IndexManager

FALLBACK_ROOT = Path("/mnt/studio5000/Logix Designer/ENU")  # dev share on the author's machine


def _config(db_dir: Path | None = None) -> Config:
    env = os.environ.get("S5K_HELP_ROOT")
    root = Path(env) if env else None
    cfg = Config(help_root=root, db_dir=db_dir)
    if not discover(cfg) and root is None and FALLBACK_ROOT.is_dir():
        cfg.help_root = FALLBACK_ROOT
    return cfg


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
