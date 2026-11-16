"""Source adapter interface. A HelpSource turns one help-format on disk into
neutral records that the indexer stores; a legacy (v31-v36) adapter can be
added later by implementing the same protocol."""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RawPage:
    path: str  # help-root-relative posix path (canonical)
    title: str = ""
    description: str = ""
    identifier: str = ""  # DC.identifier, e.g. ait_455128
    toc_id: str = ""  # wh-toc-id
    relation: str = ""  # help-root-relative path of the parent page (DC.relation)
    markdown: str = ""  # links are emitted as [text](page:<path>) placeholders
    links: list[str] = field(default_factory=list)  # resolved target paths
    # legacy format: ancestors as (title, resolved path), root first
    crumb_trail: list[tuple[str, str]] = field(default_factory=list)
    section: str = ""  # optional section override (legacy); "" -> derived from path


def parallel_map_pages(
    fn: Callable[[str], RawPage | None],
    files: list[str],
    workers: int,
    progress: Callable[[int, int], None] | None = None,
) -> Iterator[RawPage]:
    """Apply fn to each file, in order, using a spawn process pool when worthwhile.

    fn must be picklable. Falls back to serial if the pool cannot start."""
    total = len(files)
    done = 0
    if workers > 1 and total > 200:
        try:
            ctx = multiprocessing.get_context("spawn")
            with ctx.Pool(workers) as pool:
                for page in pool.imap(fn, files, chunksize=32):
                    done += 1
                    if progress and done % 100 == 0:
                        progress(done, total)
                    if page is not None:
                        yield page
            return
        except OSError, ImportError, PermissionError:
            if done:
                raise
    for rel in files:
        page = fn(rel)
        done += 1
        if progress and done % 100 == 0:
            progress(done, total)
        if page is not None:
            yield page


@dataclass
class TocNode:
    node_id: str
    parent_id: str | None  # None for top level (children of the virtual root)
    title: str
    href: str  # help-root-relative path or ""
    ord: int


class HelpSource(Protocol):
    version: str
    root: object

    def fingerprint(self) -> str:
        """Cheap string that changes when the help content changes."""

    def iter_pages(self) -> Iterator[RawPage]: ...

    def page_count(self) -> int: ...

    def toc(self) -> list[TocNode]: ...

    def help_ids(self) -> list[tuple[str, str]]:
        """(help_id, path) pairs."""

    def keywords(self) -> dict[str, list[str]]:
        """path -> list of keyword-index term paths."""

    def csv_toc_paths(self) -> dict[str, list[str]]:
        """path -> fallback breadcrumb (list of titles, ancestors only)."""
