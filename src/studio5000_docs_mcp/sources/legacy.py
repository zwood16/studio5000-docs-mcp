"""Adapter for the legacy flat-.htm help format (Logix Designer v31-v36)."""

from __future__ import annotations

import csv
import html
import json
import os
import re
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

from ..extract import parse_legacy_html
from .base import RawPage, TocNode, parallel_map_pages
from .oxygen import is_oxygen_root

NAV_PAGES = {"index.htm", "indexpage.htm", "search_results.htm", "heading.htm"}
_LOOSE_DUP = re.compile(r"^(.+)_\d+\.htm$", re.I)


def is_legacy_root(root: Path) -> bool:
    return (root / "toc.json").is_file() and not is_oxygen_root(root)


def _parse_one(root: str, rel: str) -> RawPage | None:
    try:
        with open(os.path.join(root, rel), "rb") as f:
            text = f.read().decode("utf-8", errors="replace").lstrip("﻿")
        return parse_legacy_html(text, rel)
    except Exception as e:  # keep the build going; report via placeholder page
        return RawPage(
            path=rel, title=f"[parse error] {rel}", markdown=f"parse error: {e}"
        )


def _section_for_title(title: str) -> str:
    t = title.strip()
    if t in ("Logix Designer", "Studio 5000 Logix Designer"):
        return "help/rs5000"
    if t == "Instruction Set":
        return "help/clinset"
    if t == "Module Information":
        return "help/modules"
    if t == "Quick Start Steps":
        return "quickstart"
    if "glossary" in t.lower():
        return "glossary"
    return "other"


class LegacySource:
    def __init__(
        self,
        root: Path,
        version: str,
        workers: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ):
        self.root = Path(root)
        self.version = version
        self.workers = workers if workers is not None else min(8, os.cpu_count() or 1)
        self.progress = progress
        self._files: list[str] | None = None
        self._max_mtime = 0
        self._sections: dict[str, str] | None = None

    # ---------- scan / fingerprint ----------
    def _scan(self) -> None:
        if self._files is not None:
            return
        files: list[str] = []
        mx = 0
        with os.scandir(self.root) as it:
            for e in it:
                if (
                    e.name.lower().endswith(".htm")
                    and e.name.lower() not in NAV_PAGES
                    and e.is_file()
                ):
                    files.append(e.name)
                    mx = max(mx, e.stat().st_mtime_ns)
        files.sort()
        self._files, self._max_mtime = files, mx

    def fingerprint(self) -> str:
        self._scan()
        parts = [f"legacy:{len(self._files)}:{self._max_mtime}"]
        for name in ("toc.json", "rs5000.csv", "keywords.csv"):
            try:
                stt = os.stat(self.root / name)
                parts.append(f"{stt.st_size}.{stt.st_mtime_ns}")
            except OSError:
                parts.append("missing")
        return ":".join(parts)

    def page_count(self) -> int:
        self._scan()
        return len(self._files)

    # ---------- pages ----------
    def iter_pages(self) -> Iterator[RawPage]:
        self._scan()
        sections = self._section_map()
        fn = partial(_parse_one, str(self.root))
        for page in parallel_map_pages(fn, self._files, self.workers, self.progress):
            page.section = self._section_of(page.path, sections)
            yield page

    @staticmethod
    def _section_of(path: str, sections: dict[str, str]) -> str:
        if path in sections:
            return sections[path]
        m = _LOOSE_DUP.match(path)
        if m and f"{m.group(1)}.htm" in sections:
            return sections[f"{m.group(1)}.htm"]
        return "other"

    # ---------- TOC ----------
    def _toc_json(self) -> list[dict]:
        f = self.root / "toc.json"
        if not f.exists():
            return []
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []

    def _section_map(self) -> dict[str, str]:
        """path -> section; first TOC occurrence under a visible top-level node wins."""
        if self._sections is not None:
            return self._sections
        out: dict[str, str] = {}

        def walk(node: dict, sec: str):
            url = (node.get("url") or "").split("#", 1)[0]
            if url and url not in out:
                out[url] = sec
            for ch in node.get("children") or []:
                walk(ch, sec)

        for top in self._toc_json():
            if top.get("hidden"):
                continue
            walk(top, _section_for_title(html.unescape(top.get("text", ""))))
        self._sections = out
        return out

    def toc(self) -> list[TocNode]:
        nodes: list[TocNode] = []

        def add(items: list[dict], parent: str | None, top: bool):
            for i, t in enumerate(items):
                if top and t.get("hidden"):
                    continue
                nid = f"t{t.get('id')}"
                href = (t.get("url") or "").split("#", 1)[0]
                nodes.append(
                    TocNode(
                        nid, parent, html.unescape(t.get("text", "")).strip(), href, i
                    )
                )
                add(t.get("children") or [], nid, False)

        add(self._toc_json(), None, True)
        return nodes

    # ---------- help ids ----------
    def help_ids(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        f = self.root / "rs5000.csv"
        if f.exists():
            with open(f, newline="", encoding="utf-8-sig", errors="replace") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        out.append((row[0].strip(), row[1].strip().split("#", 1)[0]))
        return out

    def csv_toc_paths(self) -> dict[str, list[str]]:
        return {}

    # ---------- keyword index ----------
    def keywords(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        f = self.root / "keywords.csv"
        if not f.exists():
            return out
        with open(f, newline="", encoding="utf-8-sig", errors="replace") as fh:
            for row in csv.DictReader(fh):
                url = (row.get("URL") or "").strip().split("#", 1)[0]
                kw = (row.get("Keyword") or "").strip()
                if url and kw:
                    lst = out.setdefault(url, [])
                    if kw not in lst:
                        lst.append(kw)
        return out
