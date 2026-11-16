"""Find Logix Designer help folders and their versions."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, normalize_version
from .sources.legacy import is_legacy_root
from .sources.oxygen import is_oxygen_root

_VER = re.compile(r"^v(\d+)$", re.I)

MIN_VERSION = 31  # older help (v30 and below) is .chm only or out of scope


@dataclass
class VersionInfo:
    version: str  # "v37"
    root: Path  # the rs5000 folder
    format: str  # "oxygen" | "legacy" | "unknown"

    @property
    def number(self) -> int:
        m = _VER.match(self.version)
        return int(m.group(1)) if m else -1


def _format_of(root: Path) -> str:
    if is_oxygen_root(root):
        return "oxygen"
    if is_legacy_root(root):
        return "legacy"
    return "unknown"


def _version_from_path(p: Path) -> str | None:
    for part in reversed(p.parts):
        if _VER.match(part):
            return part.lower()
    return None


def _windows_probe_bases() -> list[Path]:
    # Default Studio 5000 install location (confirmed on Windows 11, v30-v37 under
    # Program Files (x86)). Both help trees live under this folder.
    # Set S5K_HELP_ROOT for non-default installs.
    bases = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            bases.append(Path(base) / "Rockwell Software" / "Studio 5000")
    return bases


def _scan_enu(enu: Path) -> list[VersionInfo]:
    found: list[VersionInfo] = []
    pattern = os.path.join(glob.escape(str(enu)), "v*", "Bin", "Help", "ENU", "rs5000")
    for d in glob.glob(pattern):
        p = Path(d)
        ver = _version_from_path(p)
        if ver and p.is_dir():
            found.append(VersionInfo(ver, p, _format_of(p)))
    return found


def _scan_help_enu(enu: Path) -> list[VersionInfo]:
    """Scan a `Help/ENU` folder for `v*/rs5000` (v31, v32)."""
    found: list[VersionInfo] = []
    pattern = os.path.join(glob.escape(str(enu)), "v*", "rs5000")
    for d in glob.glob(pattern):
        p = Path(d)
        ver = _version_from_path(p)
        if ver and p.is_dir():
            found.append(VersionInfo(ver, p, _format_of(p)))
    return found


def _scan_studio5000(base: Path) -> list[VersionInfo]:
    """Scan a Studio 5000 install folder; Logix Designer tree wins on duplicates."""
    by_ver: dict[str, VersionInfo] = {}
    for info in _scan_help_enu(base / "Help" / "ENU"):
        by_ver[info.version] = info
    for info in _scan_enu(base / "Logix Designer" / "ENU"):
        by_ver[info.version] = info
    return [v for v in by_ver.values() if v.number >= MIN_VERSION]


def _studio5000_base(root: Path) -> Path | None:
    if (root / "Logix Designer").is_dir() or (root / "Help").is_dir():
        return root
    if root.name.lower() == "enu" and root.parent.name.lower() == "logix designer":
        return root.parent.parent
    if root.name.lower() == "logix designer":
        return root.parent
    return None


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
                base = _studio5000_base(root)
                if base:
                    found.extend(_scan_studio5000(base))
                else:
                    for cand in (root, root / "ENU", root / "Logix Designer" / "ENU"):
                        found.extend(
                            v for v in _scan_enu(cand) if v.number >= MIN_VERSION
                        )
                        if found:
                            break
    elif os.name == "nt":
        for base in _windows_probe_bases():
            found.extend(_scan_studio5000(base))
    return {v.version: v for v in sorted(found, key=lambda v: v.number)}


def default_version(
    versions: dict[str, VersionInfo], requested: str | None
) -> str | None:
    requested = normalize_version(requested)
    if requested and requested in versions:
        return requested
    ox = [v for v in versions.values() if v.format == "oxygen"]
    return max(ox, key=lambda v: v.number).version if ox else None
