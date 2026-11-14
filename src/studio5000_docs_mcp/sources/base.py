"""Source adapter interface. A HelpSource turns one help-format on disk into
neutral records that the indexer stores; a legacy (v33-v36) adapter can be
added later by implementing the same protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol


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
