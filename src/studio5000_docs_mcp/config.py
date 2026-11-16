"""Configuration from environment variables and CLI flags."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir

APP_NAME = "studio5000-docs-mcp"


@dataclass
class Config:
    help_root: Path | None = None  # S5K_HELP_ROOT / --root
    version: str | None = None  # S5K_VERSION / --version (e.g. "v37" or "37")
    db_dir: Path | None = None  # S5K_DB_DIR
    force_rebuild: bool = False
    transport: str = "stdio"
    build_only: bool = False

    def resolved_db_dir(self) -> Path:
        return self.db_dir if self.db_dir else Path(user_cache_dir(APP_NAME))


_VERSION_RE = re.compile(r"^v?(\d+)(?:\.\d+)*$")


def normalize_version(v: str | None) -> str | None:
    """'v37', '37', '37.00', 'v33.01' -> 'v37' / 'v33' (major only).

    Anything else is returned stripped and lowercased so lookups fail as unknown."""
    if not v:
        return None
    v = v.strip().lower()
    if not v:
        return None
    m = _VERSION_RE.match(v)
    return f"v{int(m.group(1))}" if m else v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "MCP server for searching the Studio 5000 Logix Designer help "
            "(Oxygen WebHelp v37; legacy v31+ detected)."
        ),
    )
    p.add_argument(
        "--root",
        help=(
            "Help root: the 'Studio 5000' install folder, the "
            "'Logix Designer/ENU' folder, or one rs5000 folder (env S5K_HELP_ROOT)"
        ),
    )
    p.add_argument("--version", help="Default help version, e.g. v37 (env S5K_VERSION)")
    p.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Rebuild the index even if the cache is fresh",
    )
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument(
        "--build-only",
        action="store_true",
        help="Build the index, print statistics and timing, then exit",
    )
    return p


def load_config(argv: list[str] | None = None) -> Config:
    a = build_parser().parse_args(argv)
    root = a.root or os.environ.get("S5K_HELP_ROOT")
    db_dir = os.environ.get("S5K_DB_DIR")
    return Config(
        help_root=Path(root).expanduser() if root else None,
        version=normalize_version(a.version or os.environ.get("S5K_VERSION")),
        db_dir=Path(db_dir).expanduser() if db_dir else None,
        force_rebuild=a.force_rebuild,
        transport=a.transport,
        build_only=a.build_only,
    )
