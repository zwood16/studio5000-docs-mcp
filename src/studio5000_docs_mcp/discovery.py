"""Find Logix Designer help folders and their versions."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, normalize_version
from .sources.oxygen import is_oxygen_root

_VER = re.compile(r"^v(\d+)$", re.I)


@dataclass
class VersionInfo:
    version: str  # "v37"
    root: Path  # the rs5000 folder
    format: str  # "oxygen" | "legacy"

    @property
    def number(self) -> int:
        m = _VER.match(self.version)
        return int(m.group(1)) if m else -1


def _format_of(root: Path) -> str:
    return "oxygen" if is_oxygen_root(root) else "legacy"


def _version_from_path(p: Path) -> str | None:
    for part in reversed(p.parts):
        if _VER.match(part):
            return part.lower()
    return None


def _windows_probe_roots() -> list[Path]:
    # UNVERIFIED: this default install location has not been checked on a real
    # Windows machine. Set S5K_HELP_ROOT if it does not match your install.
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(Path(base) / "Rockwell Software" / "Studio 5000" / "Logix Designer" / "ENU")
    return roots


def _scan_enu(enu: Path) -> list[VersionInfo]:
    found: list[VersionInfo] = []
    pattern = os.path.join(glob.escape(str(enu)), "v*", "Bin", "Help", "ENU", "rs5000")
    for d in glob.glob(pattern):
        p = Path(d)
        ver = _version_from_path(p)
        if ver and p.is_dir():
            found.append(VersionInfo(ver, p, _format_of(p)))
    return found


def discover(cfg: Config) -> dict[str, VersionInfo]:
    """Return {version: VersionInfo}. Empty when nothing is found."""
    found: list[VersionInfo] = []
    if cfg.help_root:
        root = cfg.help_root
        if root.is_dir():
            if is_oxygen_root(root) or root.name.lower() == "rs5000":
                ver = _version_from_path(root) or cfg.version or "v?"
                found.append(VersionInfo(ver, root, _format_of(root)))
            else:
                for cand in (root, root / "ENU", root / "Logix Designer" / "ENU"):
                    found.extend(_scan_enu(cand))
                    if found:
                        break
    elif os.name == "nt":
        for enu in _windows_probe_roots():
            found.extend(_scan_enu(enu))
    return {v.version: v for v in sorted(found, key=lambda v: v.number)}


def default_version(versions: dict[str, VersionInfo], requested: str | None) -> str | None:
    requested = normalize_version(requested)
    if requested and requested in versions:
        return requested
    ox = [v for v in versions.values() if v.format == "oxygen"]
    return max(ox, key=lambda v: v.number).version if ox else None
