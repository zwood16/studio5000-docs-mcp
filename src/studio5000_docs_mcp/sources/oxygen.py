"""Adapter for Oxygen WebHelp output (Logix Designer v37+)."""

from __future__ import annotations

import csv
import json
import multiprocessing
import os
import re
import xml.etree.ElementTree as ET
from functools import partial
from pathlib import Path
from typing import Callable, Iterator

from ..extract import parse_html
from .base import RawPage, TocNode

CONTENT_DIRS = ("help", "publications", "common-content")
NAVLINKS_DIR = Path("oxygen-webhelp") / "app" / "nav-links" / "json"
_TAG = re.compile(r"<[^>]+>")


def is_oxygen_root(root: Path) -> bool:
    return (root / "oxygen-webhelp").is_dir()


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", s or "")).strip()


def _load_js_object(path: Path):
    """Parse `define({...});` or `oxy_webhelp_navlinks = {...};` files."""
    text = path.read_text(encoding="utf-8", errors="replace")
    i = text.find("{")
    j = text.rfind("}")
    if i < 0 or j < i:
        raise ValueError(f"no JSON object in {path}")
    return json.loads(text[i : j + 1])


def _parse_one(root: str, rel: str) -> RawPage | None:
    try:
        with open(os.path.join(root, rel), "rb") as f:
            html = f.read().decode("utf-8", errors="replace")
        return parse_html(html, rel)
    except Exception as e:  # keep the build going; report via empty page
        return RawPage(path=rel, title=f"[parse error] {rel}", markdown=f"parse error: {e}")


class OxygenSource:
    def __init__(self, root: Path, version: str, workers: int | None = None,
                 progress: Callable[[int, int], None] | None = None):
        self.root = Path(root)
        self.version = version
        self.workers = workers if workers is not None else min(8, os.cpu_count() or 1)
        self.progress = progress
        self._files: list[str] | None = None
        self._max_mtime = 0

    # ---------- scan / fingerprint ----------
    def _scan(self) -> None:
        if self._files is not None:
            return
        files: list[str] = []
        mx = 0

        def walk(d: str, rel: str):
            nonlocal mx
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        walk(e.path, f"{rel}/{e.name}")
                    elif e.name.endswith(".html"):
                        files.append(f"{rel}/{e.name}")
                        mx = max(mx, e.stat().st_mtime_ns)

        for d in CONTENT_DIRS:
            p = self.root / d
            if p.is_dir():
                walk(str(p), d)
        files.sort()
        self._files, self._max_mtime = files, mx

    def fingerprint(self) -> str:
        self._scan()
        parts = [f"oxygen:{len(self._files)}:{self._max_mtime}"]
        for name in ("context-help-map.xml", "rs5000.csv", "index.xml"):
            try:
                stt = os.stat(self.root / name)
                parts.append(f"{stt.st_size}.{stt.st_mtime_ns}")
            except OSError:
                parts.append("missing")
        n, mx = 0, 0
        try:
            with os.scandir(self.root / NAVLINKS_DIR) as it:
                for e in it:
                    if e.is_file():
                        n += 1
                        mx = max(mx, e.stat().st_mtime_ns)
        except OSError:
            pass
        parts.append(f"nav{n}.{mx}")
        return ":".join(parts)

    def page_count(self) -> int:
        self._scan()
        return len(self._files)

    # ---------- pages ----------
    def iter_pages(self) -> Iterator[RawPage]:
        self._scan()
        files = self._files
        total = len(files)
        fn = partial(_parse_one, str(self.root))
        done = 0
        if self.workers > 1 and total > 200:
            try:
                ctx = multiprocessing.get_context("spawn")
                with ctx.Pool(self.workers) as pool:
                    for page in pool.imap(fn, files, chunksize=32):
                        done += 1
                        if self.progress and done % 100 == 0:
                            self.progress(done, total)
                        if page is not None:
                            yield page
                return
            except (OSError, ImportError, PermissionError):
                if done:
                    raise
        for rel in files:
            page = fn(rel)
            done += 1
            if self.progress and done % 100 == 0:
                self.progress(done, total)
            if page is not None:
                yield page

    # ---------- TOC ----------
    def toc(self) -> list[TocNode]:
        d = self.root / NAVLINKS_DIR
        root_file = d / "nav-links.js"
        if not root_file.exists():
            return []
        nodes: list[TocNode] = []
        seen: set[str] = set()

        def children_of(topic: dict) -> list[dict] | None:
            inline = topic.get("topics")
            if inline:
                return inline
            # menu.hasChildren is unreliable (false for some nodes that do have a
            # child file), so always look for <tocID>.js
            f = d / f"{topic.get('tocID')}.js"
            if topic.get("tocID") and f.exists():
                try:
                    data = _load_js_object(f)
                except (ValueError, OSError):
                    return None
                for v in data.values():
                    if isinstance(v, dict) and "topics" in v:
                        return v["topics"]
            return None

        def add(topics: list[dict], parent: str | None):
            for i, t in enumerate(topics):
                nid = t.get("tocID") or t.get("attributes", {}).get("data-id") or ""
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                href = (t.get("href") or "").replace("\\", "/")
                href = href.split("#", 1)[0]
                nodes.append(TocNode(nid, parent, _strip_html(t.get("title", "")), href, i))
                kids = children_of(t)
                if kids:
                    add(kids, nid)

        root = _load_js_object(root_file)
        add(root.get("topics", []), None)
        return nodes

    def toc_title(self) -> str:
        try:
            return _strip_html(_load_js_object(self.root / NAVLINKS_DIR / "nav-links.js").get("title", ""))
        except (OSError, ValueError):
            return ""

    # ---------- help ids ----------
    def help_ids(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        f = self.root / "context-help-map.xml"
        if f.exists():
            try:
                for _, el in ET.iterparse(f):
                    if el.tag == "appContext":
                        hid = (el.get("helpID") or "").strip()
                        p = (el.get("path") or "").replace("\\", "/").split("#", 1)[0].strip()
                        if hid and p:
                            out.append((hid, p))
                        el.clear()
            except ET.ParseError:
                pass
        f = self.root / "rs5000.csv"
        if f.exists():
            with open(f, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        out.append((row[0].strip(), row[1].strip().replace("\\", "/").split("#", 1)[0]))
        return out

    def csv_toc_paths(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        f = self.root / "rs5000.csv"
        if not f.exists():
            return out
        with open(f, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.reader(fh):
                if len(row) >= 3 and row[1].strip() and row[2].strip():
                    segs = [s for s in row[2].strip().replace("\\", "/").split("/") if s]
                    segs = segs[:-1]  # drop the page itself
                    if segs and segs[0] == "contents-ditamap":
                        segs = segs[1:]
                    out[row[1].strip().split("#", 1)[0]] = [s.replace("-", " ").replace("_", " ").strip().title() for s in segs]
        return out

    # ---------- keyword index ----------
    def keywords(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        f = self.root / "index.xml"
        if not f.exists():
            return out
        try:
            tree = ET.parse(f)
        except ET.ParseError:
            return out

        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def walk(el, names: list[str]):
            for ch in el:
                t = local(ch.tag)
                if t == "term":
                    walk(ch, names + [ch.get("name") or ""])
                elif t == "target" and ch.text and names:
                    p = ch.text.strip().replace("\\", "/").split("#", 1)[0]
                    term = " ".join(n for n in names if n)
                    lst = out.setdefault(p, [])
                    if term not in lst:
                        lst.append(term)

        walk(tree.getroot(), [])
        return out
