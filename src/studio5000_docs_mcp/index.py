"""SQLite FTS5 index: build, staleness check, search, lookups."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, normalize_version
from .discovery import VersionInfo, default_version, discover
from .extract import (
    clean_punct,
    expand_identifiers,
    is_stop,
    markdown_to_text,
    query_term,
    query_tokens,
)
from .sources.base import HelpSource, RawPage
from .sources.legacy import LegacySource
from .sources.oxygen import OxygenSource

SCHEMA_VERSION = 5
TOKENIZER = "porter unicode61 remove_diacritics 2 tokenchars '_.-'"
# column order: title, mnemonic, keywords, breadcrumb, body, extra
BM25_WEIGHTS = (10.0, 8.0, 6.0, 2.0, 2.0, 1.0)
SNIPPET_COL = 4
MNEMONIC_BONUS = 6.0
# Title coverage: tier 1/2 candidates (top RESCORE_POOL by bm25) are rescored as
# bm25 - TITLE_COVERAGE_WEIGHT * (IDF-weighted fraction of the query's content words
# found in the page title).
# Only multi-word queries are rescored; synonyms never count toward coverage.
TITLE_COVERAGE_WEIGHT = 25.0
RESCORE_POOL = 150
TITLE_POOL = 30  # per query term, OR tier only

SCHEMA = f"""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE pages(
  rid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, version TEXT, path TEXT UNIQUE NOT NULL,
  title TEXT, mnemonic TEXT, kind TEXT, section TEXT, breadcrumb TEXT, crumbs TEXT,
  description TEXT, markdown TEXT, chars INTEGER, links TEXT, parent_id TEXT, toc_node TEXT, depth INTEGER, content_hash TEXT);
CREATE TABLE mnemonics(mnemonic TEXT NOT NULL, page_id TEXT NOT NULL);
CREATE INDEX mnemonics_m ON mnemonics(mnemonic);
CREATE TABLE toc(node_id TEXT PRIMARY KEY, parent_id TEXT, page_id TEXT, title TEXT, ord INTEGER);
CREATE INDEX toc_parent ON toc(parent_id, ord);
CREATE TABLE help_ids(help_id TEXT NOT NULL, page_id TEXT NOT NULL, PRIMARY KEY(help_id, page_id));
CREATE VIRTUAL TABLE pages_fts USING fts5(title, mnemonic, keywords, breadcrumb, body, extra,
  tokenize="{TOKENIZER}");
"""  # noqa: E501  (SQL text kept byte-identical)

_MN_RE = re.compile(r"\(([A-Z0-9_ ]+)\)")


_MN_DASH_RE = re.compile(r"[-\u2013]\s?([A-Z][A-Z0-9_]+)$")


def extract_mnemonics(
    title: str, path: str = "", section: str | None = None
) -> list[str]:
    """All parenthesised all-caps mnemonics in a title.

    E.g. 'Get ... (GSV) and Set ... (SSV)'. Instruction-set pages titled like
    'DINT to String-DTOS' use a trailing '-MNEMONIC' instead."""
    if section is None:
        section = section_of(path)
    out = []
    for m in _MN_RE.findall(title):
        m = m.strip()
        if len(m) >= 2 and re.search(r"[A-Z]", m) and m not in out:
            out.append(m)
    if not out and section == "help/clinset":
        m = _MN_DASH_RE.search(title.strip())
        if m:
            out.append(m.group(1))
    return out


# last-resort breadcrumb label per section; titles match the top-level TOC where one
# exists
SECTION_LABELS = {
    "common-content": "Common content",
    "help/clinset": "Instruction Set",
    "help/rs5000": "Studio 5000 Logix Designer",
    "help/modules": "Module Information",
    "publications": "Publications",
    "quickstart": "Quick Start Steps",
    "glossary": "Glossary",
}


def section_of(path: str) -> str:
    parts = path.split("/")
    if parts[0] in ("help", "publications") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


_FAULT_START_RE = re.compile(r"^(failed to|unable to|cannot|can't|could not|error\b)")
_FAULT_WORD_RE = re.compile(r"\b(faults?|errors?|unhandled exception)\b")
_DIALOG_RE = re.compile(r"\b(dialog box|dialog|wizard|tab)\b|\b(page|window|pane)$")
_PROPS_RE = re.compile(
    r"\bProperties( -|$)"
)  # case-sensitive: "Module Properties - General", not "Edit routine properties"
_CATALOG_RE = re.compile(r"\b\d{4}[A-Z]?-[A-Z0-9]+")


def classify(path: str, title: str, mnemonic: str, section: str | None = None) -> str:
    """Page kind from path and title. Order matters: the first matching rule wins.

    instruction: help/clinset page with a mnemonic (flow-chart image pages excluded)
    fault:       module-errors folder, error-message titles ("Failed to ...") or
                 fault/error words (a dialog/tab page about faults stays a dialog)
    command:     "... command" pages
    dialog:      dialog box, wizard, tab, page, window or pane pages; a title-case
                 "Properties" heading
    module:      anything under help/modules, or a page naming a catalog-numbered module
    concept:     the rest (overviews, procedures, attribute references)
    """
    if section is None:
        section = section_of(path)
    t = title.lower().strip()
    pl = path.lower()
    if section == "help/clinset" and mnemonic and "flow chart" not in t:
        return "instruction"
    if "module-errors" in pl or _FAULT_START_RE.search(t):
        return "fault"
    if (
        t.endswith(" command")
        or t.endswith(" commands")
        or pl.endswith("-command.html")
    ):
        return "command"
    if _DIALOG_RE.search(t) or _PROPS_RE.search(title):
        return "dialog"
    if _FAULT_WORD_RE.search(t):
        return "fault"
    if section == "help/modules" or _CATALOG_RE.search(title):
        return "module"
    return "concept"


def parse_version_arg(v: str | None) -> str | None:
    return normalize_version(v)


# ---------------------------------------------------------------- build


def build_index(
    source: HelpSource, db_path: Path, progress=None, *, fingerprint: str
) -> dict:
    """Build the DB into a temp file next to db_path, then atomically rename."""
    t0 = time.time()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        tmp.unlink()

    pages: list[RawPage] = []
    for p in source.iter_pages():
        pages.append(p)
    t_parse = time.time() - t0

    # ---- ids
    id_counts: dict[str, int] = {}
    for p in pages:
        if p.identifier:
            id_counts[p.identifier] = id_counts.get(p.identifier, 0) + 1
    path_to_id: dict[str, str] = {}
    for p in pages:
        path_to_id[p.path] = (
            p.identifier if p.identifier and id_counts[p.identifier] == 1 else p.path
        )
    by_path = {p.path: p for p in pages}
    sec_of = {p.path: p.section or section_of(p.path) for p in pages}

    # ---- toc
    nodes = source.toc()
    node_by_id = {n.node_id: n for n in nodes}
    href_to_node: dict[str, str] = {}
    for n in nodes:
        if n.href and n.href in by_path and n.href not in href_to_node:
            href_to_node[n.href] = n.node_id

    def toc_crumbs(node_id: str) -> list[tuple[str, str | None]]:
        chain = []
        cur = node_by_id[node_id].parent_id
        guard = 0
        while cur and guard < 100:
            n = node_by_id[cur]
            chain.append((n.title, path_to_id.get(n.href) if n.href else None))
            cur = n.parent_id
            guard += 1
        return chain[::-1]

    csv_paths = source.csv_toc_paths()
    memo: dict[str, list[tuple[str, str | None]]] = {}
    method: dict[str, str] = {}  # path -> how its breadcrumb was derived

    def crumbs_for(
        path: str, stack: tuple[str, ...] = ()
    ) -> list[tuple[str, str | None]]:
        if path in memo:
            return memo[path]
        res: list[tuple[str, str | None]] = []
        if path in href_to_node:
            res = toc_crumbs(href_to_node[path])
            if res:
                method[path] = "toc"
        else:
            page = by_path.get(path)
            rel = page.relation if page else ""
            if rel and rel in by_path and rel != path and rel not in stack:
                par = by_path[rel]
                res = crumbs_for(rel, stack + (path,)) + [(par.title, path_to_id[rel])]
                method[path] = "relation"
            elif path in csv_paths:
                res = [(t, None) for t in csv_paths[path]]
                if res:
                    method[path] = "csv"
            elif page is not None and page.crumb_trail:
                res = [(t, path_to_id.get(cp)) for t, cp in page.crumb_trail]
                method[path] = "page"
        memo[path] = res
        return res

    # ---- fallbacks for pages without a breadcrumb: inbound links, then a section label
    primary = {p.path: crumbs_for(p.path) for p in pages}  # toc / relation / csv only
    inbound: dict[str, list[str]] = {}
    for p in pages:
        for link in p.links:
            if link in by_path and link != p.path:
                inbound.setdefault(link, []).append(p.path)
    final: dict[str, list[tuple[str, str | None]]] = {}
    for p in sorted(pages, key=lambda x: x.path):
        res = primary[p.path]
        if (
            not res and p.path not in href_to_node
        ):  # TOC pages with no ancestors are roots; leave them empty
            sec = sec_of[p.path]
            cands = sorted({c for c in inbound.get(p.path, []) if primary[c]})
            if cands:
                sec_counts: dict[str, int] = {}
                for c in cands:
                    sec_counts[sec_of[c]] = sec_counts.get(sec_of[c], 0) + 1
                best = min(
                    cands,
                    key=lambda c: (
                        sec_of[c] != sec,
                        -sec_counts[sec_of[c]],
                        len(primary[c]),
                        c,
                    ),
                )
                res = primary[best] + [(by_path[best].title, path_to_id[best])]
                method[p.path] = "inbound"
            else:
                label = SECTION_LABELS.get(sec) or SECTION_LABELS.get(sec.split("/")[0])
                if label:
                    res = [(label, None)]
                    method[p.path] = "section"
        final[p.path] = res

    # ---- link rewrite
    link_re = re.compile(r"\]\(page:([^)]+)\)")

    def rewrite(md: str) -> str:
        def sub(m: re.Match) -> str:
            tid = path_to_id.get(m.group(1))
            return f"](page:{tid})" if tid else "]()"

        out = link_re.sub(sub, md)
        # unresolved links: drop the markup, keep the text
        return re.sub(r"\[([^\]]*)\]\(\)", r"\1", out)

    keywords = source.keywords()
    help_ids = source.help_ids()

    conn = sqlite3.connect(tmp)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(SCHEMA)
    version = source.version

    n_with_crumbs = 0
    n_mn = 0
    for rid, p in enumerate(sorted(pages, key=lambda x: x.path), start=1):
        pid = path_to_id[p.path]
        sec = sec_of[p.path]
        mns = extract_mnemonics(p.title, p.path, sec)
        mnemonic = " ".join(mns)
        kind = classify(p.path, p.title, mnemonic, sec)
        crumbs = final[p.path]
        if crumbs:
            n_with_crumbs += 1
        breadcrumb = " > ".join(t for t, _ in crumbs)
        md = rewrite(p.markdown)
        out_ids = [path_to_id[x] for x in p.links if x in path_to_id]
        parent_id = path_to_id.get(p.relation) if p.relation else None
        kw = " ; ".join(keywords.get(p.path, []))
        body = markdown_to_text(md)
        title_f = clean_punct(p.title)
        conn.execute(
            "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                pid,
                version,
                p.path,
                p.title,
                mnemonic,
                kind,
                sec,
                breadcrumb,
                json.dumps(crumbs),
                p.description,
                md,
                len(md),
                json.dumps(out_ids),
                parent_id,
                href_to_node.get(p.path),
                len(crumbs),
                hashlib.sha1(md.encode("utf-8")).hexdigest(),
            ),
        )
        for m in mns:
            conn.execute("INSERT INTO mnemonics VALUES (?,?)", (m, pid))
            n_mn += 1
        bc_f = clean_punct(breadcrumb)
        kw_f = clean_punct(kw)
        title_col = f"{title_f} {expand_identifiers(title_f)}".strip()
        extra = " ".join(
            x
            for x in (
                expand_identifiers(body),
                expand_identifiers(kw_f),
                expand_identifiers(bc_f),
            )
            if x
        )
        conn.execute(
            "INSERT INTO pages_fts(rowid,title,mnemonic,keywords,breadcrumb,body,extra)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                rid,
                title_col,
                mnemonic,
                f"{kw_f} {expand_identifiers(kw_f)}".strip(),
                bc_f,
                body,
                extra,
            ),
        )
        if progress and rid % 500 == 0:
            progress("indexing", rid, len(pages))

    for n in nodes:
        pg = path_to_id.get(n.href) if n.href else None
        conn.execute(
            "INSERT INTO toc VALUES (?,?,?,?,?)",
            (n.node_id, n.parent_id, pg, n.title, n.ord),
        )
    seen_h = set()
    for hid, hp in help_ids:
        pg = path_to_id.get(hp)
        if pg and (hid, pg) not in seen_h:
            seen_h.add((hid, pg))
            conn.execute("INSERT INTO help_ids VALUES (?,?)", (hid, pg))

    conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('optimize')")
    elapsed = time.time() - t0
    stats = {
        "pages": len(pages),
        "pages_with_breadcrumb": n_with_crumbs,
        "breadcrumb_methods": {
            m: sum(1 for v in method.values() if v == m)
            for m in ("toc", "relation", "csv", "page", "inbound", "section")
        },
        "toc_nodes": len(nodes),
        "help_ids": len(seen_h),
        "mnemonic_entries": n_mn,
        "build_seconds": round(elapsed, 2),
        "parse_seconds": round(t_parse, 2),
    }
    meta = {
        "fingerprint": fingerprint,
        "schema": str(SCHEMA_VERSION),
        "version": version,
        "root": str(source.root),
        "built_at": str(int(time.time())),
        "stats": json.dumps(stats),
    }
    conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    conn.commit()
    conn.close()
    os.replace(tmp, db_path)
    return stats


def db_fingerprint(source: HelpSource) -> str:
    return f"{source.fingerprint()}:schema{SCHEMA_VERSION}:{source.root}:{TOKENIZER}"


# ---------------------------------------------------------------- query


class Index:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.version = self._meta("version")

    def close(self):
        self.conn.close()

    def _meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def stats(self) -> dict:
        with self.lock:
            c = self.conn
            kinds = {
                r[0]: r[1]
                for r in c.execute("SELECT kind, count(*) FROM pages GROUP BY kind")
            }
            sections = {
                r[0]: r[1]
                for r in c.execute(
                    "SELECT section, count(*) FROM pages "
                    "GROUP BY section ORDER BY 2 DESC"
                )
            }
            return {
                "version": self.version,
                "pages": c.execute("SELECT count(*) FROM pages").fetchone()[0],
                "with_breadcrumb": c.execute(
                    "SELECT count(*) FROM pages WHERE breadcrumb != ''"
                ).fetchone()[0],
                "toc_nodes": c.execute("SELECT count(*) FROM toc").fetchone()[0],
                "help_ids": c.execute(
                    "SELECT count(DISTINCT help_id) FROM help_ids"
                ).fetchone()[0],
                "mnemonics": c.execute(
                    "SELECT count(DISTINCT mnemonic) FROM mnemonics"
                ).fetchone()[0],
                "kinds": kinds,
                "sections": sections,
                "root": self._meta("root"),
                "built_at": int(self._meta("built_at") or 0),
                "build_stats": json.loads(self._meta("stats") or "{}"),
            }

    # ---- lookups
    def resolve_page(self, ref: str) -> sqlite3.Row | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        norm = ref.replace("\\", "/").split("#", 1)[0].lstrip("/")
        with self.lock:
            for sql, arg in (
                ("SELECT * FROM pages WHERE id=?", ref),
                ("SELECT * FROM pages WHERE path=?", norm),
                ("SELECT * FROM pages WHERE lower(id)=lower(?)", ref),
                ("SELECT * FROM pages WHERE lower(path)=lower(?)", norm),
            ):
                r = self.conn.execute(sql, (arg,)).fetchone()
                if r:
                    return r
        return None

    def instruction(self, mnemonic: str) -> list[sqlite3.Row]:
        m = mnemonic.strip().upper()
        with self.lock:
            return self.conn.execute(
                """SELECT p.* FROM mnemonics m JOIN pages p ON p.id=m.page_id WHERE m.mnemonic=?
                   ORDER BY (p.path LIKE 'help/clinset/%') DESC, (p.kind='instruction') DESC, p.depth, p.path""",  # noqa: E501
                (m,),
            ).fetchall()

    def by_help_id(self, help_id: str) -> list[sqlite3.Row]:
        with self.lock:
            rows = self.conn.execute(
                """SELECT p.* FROM help_ids h JOIN pages p ON p.id=h.page_id WHERE h.help_id=?
                   ORDER BY p.path""",  # noqa: E501
                (help_id.strip(),),
            ).fetchall()
            if not rows:
                rows = self.conn.execute(
                    """SELECT p.* FROM help_ids h JOIN pages p ON p.id=h.page_id WHERE lower(h.help_id)=lower(?)
                       ORDER BY p.path""",  # noqa: E501
                    (help_id.strip(),),
                ).fetchall()
            return rows

    def toc_children(self, node_id: str | None) -> list[dict]:
        with self.lock:
            if node_id:
                if not self.conn.execute(
                    "SELECT 1 FROM toc WHERE node_id=?", (node_id,)
                ).fetchone():
                    pg = self.resolve_page(node_id)
                    if pg is None or not pg["toc_node"]:
                        raise KeyError(node_id)
                    node_id = pg["toc_node"]
                rows = self.conn.execute(
                    "SELECT * FROM toc WHERE parent_id=? ORDER BY ord", (node_id,)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM toc WHERE parent_id IS NULL ORDER BY ord"
                ).fetchall()
            out = []
            for r in rows:
                n = self.conn.execute(
                    "SELECT count(*) FROM toc WHERE parent_id=?", (r["node_id"],)
                ).fetchone()[0]
                out.append(
                    {
                        "node_id": r["node_id"],
                        "title": r["title"],
                        "page_id": r["page_id"],
                        "children": n,
                    }
                )
            return out

    def toc_node_title(self, node_id: str) -> str | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT title FROM toc WHERE node_id=?", (node_id,)
            ).fetchone()
            return r[0] if r else None

    def titles_for(self, ids: list[str]) -> dict[str, tuple[str, str]]:
        if not ids:
            return {}
        with self.lock:
            q = ",".join("?" * len(ids))
            return {
                r["id"]: (r["title"], r["path"])
                for r in self.conn.execute(
                    f"SELECT id,title,path FROM pages WHERE id IN ({q})", ids
                )
            }

    # ---- search
    def _fts(
        self,
        match: str,
        kind: str | None,
        section: str | None,
        limit: int,
        mnemonics: list[str] | None = None,
    ) -> list[sqlite3.Row]:
        # bm25 IDF is table-wide, so a mnemonic like FOR (a common word) barely scores
        # in the mnemonic column; ALL-CAPS query tokens that equal a page mnemonic
        # therefore get a flat bonus.
        bonus_sql = ""
        bonus_args: list = []
        for m in mnemonics or []:
            bonus_sql += (
                " - (CASE WHEN ' ' || p.mnemonic || ' ' LIKE ? THEN ? ELSE 0 END)"
            )
            bonus_args += [f"% {m} %", MNEMONIC_BONUS]
        where = ["pages_fts MATCH ?"]
        args: list = [match]
        if kind:
            where.append("p.kind=?")
            args.append(kind)
        if section:
            s = section.strip().strip("/")
            where.append("(p.section=? OR p.section=? OR p.section LIKE ?)")
            args += [s, f"help/{s}", f"{s}/%"]
        w = ",".join(str(x) for x in BM25_WEIGHTS)
        sql = f"""SELECT p.rid, p.id, p.path, p.title, p.mnemonic, p.kind, p.section, p.breadcrumb, p.description,
                         p.content_hash,
                         bm25(pages_fts, {w}){bonus_sql} AS score,
                         snippet(pages_fts, {SNIPPET_COL}, '**', '**', ' ... ', 32) AS snip
                  FROM pages_fts JOIN pages p ON p.rid = pages_fts.rowid
                  WHERE {" AND ".join(where)} ORDER BY score LIMIT ?"""  # noqa: E501
        args.append(limit)
        with self.lock:
            try:
                return self.conn.execute(sql, bonus_args + args).fetchall()
            except sqlite3.OperationalError:
                return []

    def search(
        self,
        query: str,
        kind: str | None = None,
        section: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 50))
        raw_tokens = query_tokens(query)
        if not raw_tokens:
            return []
        toks = [t for t in raw_tokens if not is_stop(t)] or raw_tokens
        quoted = [f'"{t.replace('"', "")}"' for t in toks]
        terms = [
            query_term(t) for t in toks
        ]  # (original, expanded-with-synonyms) FTS fragments
        mn_toks = [
            t for t in toks if t.isupper() and len(t) > 1 and not t.startswith(".")
        ][:4]
        results: list[dict] = []
        seen: set[int] = set()
        kept: dict[tuple[str, str], dict] = {}  # (title, content hash) -> kept result

        def add(row: sqlite3.Row, tier: int):
            if row["rid"] in seen:
                return
            seen.add(row["rid"])
            key = ((row["title"] or "").lower(), row["content_hash"] or "")
            if key[1] and key in kept:  # identical copy of a better-ranked hit
                kept[key]["duplicates"] += 1
                return
            res = {
                "id": row["id"],
                "path": row["path"],
                "title": row["title"],
                "mnemonic": row["mnemonic"],
                "kind": row["kind"],
                "section": row["section"],
                "breadcrumb": row["breadcrumb"],
                "snippet": (
                    row["snip"]
                    if "snip" in row.keys() and row["snip"]  # noqa: SIM118
                    else row["description"]
                )
                or "",
                "tier": tier,
                "duplicates": 0,
            }
            results.append(res)
            if key[1]:
                kept[key] = res

        # tier 0: exact title / mnemonic matches
        tier0: list[sqlite3.Row] = []
        single = len(toks) == 1
        with self.lock:
            tier0 += self.conn.execute(
                "SELECT * FROM pages WHERE lower(title)=? "
                "ORDER BY (path LIKE 'help/clinset/%') DESC, path LIMIT 5",
                (query.strip().lower(),),
            ).fetchall()
            if single:  # a lone mnemonic: go straight to its instruction page(s)
                tier0 += self.instruction(toks[0])[:5]
        mrows = []
        for r in tier0:
            if kind and r["kind"] != kind:
                continue
            if section:
                s = section.strip().strip("/")
                if not (
                    r["section"] == s
                    or r["section"] == f"help/{s}"
                    or r["section"].startswith(s + "/")
                ):
                    continue
            mrows.append(r)
        if mrows:
            snips = {
                x["rid"]: x for x in self._fts(" OR ".join(quoted), kind, section, 200)
            }
            for r in mrows:
                s = snips.get(r["rid"])
                add(s if s is not None else r, 0)

        # tier 1: all terms (each may be satisfied by a synonym), tier 2: any term.
        # Both are rescored by title coverage; when AND alone cannot fill the limit
        # its few hits compete with the OR pool on the same adjusted score (a
        # spurious full-AND match must not outrank a better-titled page).
        expanded = [e for _, e in terms]
        t1 = self._ranked(" AND ".join(expanded), terms, kind, section, mn_toks)
        t1_ids = {r["rid"] for _, r in t1}
        cands = list(t1)
        t1_distinct = {(r["title"].lower(), r["content_hash"]) for _, r in t1}
        if len(t1_distinct) < limit and len(quoted) > 1:
            cands += [
                (sc, r)
                for sc, r in self._ranked(
                    " OR ".join(expanded), terms, kind, section, mn_toks
                )
                if r["rid"] not in t1_ids
            ]
            cands.sort(key=lambda x: x[0])
        for _, r in cands:
            add(r, 1 if r["rid"] in t1_ids else 2)
        return results[:limit]

    def _ranked(
        self,
        match: str,
        terms: list[tuple[str, str]],
        kind: str | None,
        section: str | None,
        mn_toks: list[str],
    ) -> list[tuple[float, sqlite3.Row]]:
        rows = self._fts(match, kind, section, RESCORE_POOL, mn_toks)
        if len(terms) < 2 or len(rows) < 2:
            return [(r["score"], r) for r in rows]
        # OR tier: also pool each term's best title matches (rare words in short titles)
        if " OR " in match:
            seen = {r["rid"] for r in rows}
            for orig, _ in terms:
                for r in self._fts(
                    f"title : {orig}", kind, section, TITLE_POOL, mn_toks
                ):
                    if r["rid"] not in seen:
                        seen.add(r["rid"])
                        rows.append(r)
        found = {r["rid"]: 0.0 for r in rows}
        total = 0.0
        with self.lock:
            npages = self.conn.execute("SELECT count(*) FROM pages").fetchone()[0]
            for orig, _ in terms:
                try:
                    hit = self.conn.execute(
                        "SELECT rowid FROM pages_fts WHERE pages_fts MATCH ?",
                        [f"title : {orig}"],
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                # a term no title contains (e.g. an identifier): still part of the
                # denominator
                if not hit:
                    total += math.log(1 + npages)
                    continue
                w = math.log(1 + npages / len(hit))  # rarer title words count more
                total += w
                for (rid,) in hit:
                    if rid in found:
                        found[rid] += w
        if not total:
            return [(r["score"], r) for r in rows]
        return sorted(
            (
                (r["score"] - TITLE_COVERAGE_WEIGHT * found[r["rid"]] / total, r)
                for r in rows
            ),
            key=lambda x: x[0],
        )


# ---------------------------------------------------------------- manager


@dataclass
class BuildState:
    phase: str = "pending"  # pending | scanning | parsing | indexing | ready | error
    done: int = 0
    total: int = 0
    started: float = 0.0
    finished: float = 0.0
    error: str | None = None
    rebuilt: bool = False
    stats: dict = field(default_factory=dict)


class IndexNotReady(Exception):
    pass


class IndexManager:
    RETRY_AFTER = 30.0  # seconds after a failed build before get() tries again

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.versions: dict[str, VersionInfo] = discover(cfg)
        self.default = default_version(self.versions, cfg.version)
        self._indexes: dict[str, Index] = {}
        self._states: dict[str, BuildState] = {v: BuildState() for v in self.versions}
        self._locks: dict[str, threading.Lock] = {
            v: threading.Lock() for v in self.versions
        }
        self._ready: dict[str, threading.Event] = {
            v: threading.Event() for v in self.versions
        }
        self._thread: threading.Thread | None = None
        self._signatures: dict[str, tuple | None] = {}

    def _make_source(self, version: str, progress=None):
        info = self.versions[version]
        cls = OxygenSource if info.format == "oxygen" else LegacySource
        return cls(info.root, version, progress=progress)

    def content_signature(self, version: str) -> tuple | None:
        """Lazily computed, cached content signature (None if unavailable)."""
        if version not in self._signatures:
            try:
                self._signatures[version] = self._make_source(
                    version
                ).content_signature()
            except Exception:
                self._signatures[version] = None
        return self._signatures[version]

    def db_path(self, version: str) -> Path:
        return self.cfg.resolved_db_dir() / f"{version}.sqlite"

    def state(self, version: str) -> BuildState:
        return self._states[version]

    def start_background(self) -> None:
        if self.default and self._thread is None:
            self._thread = threading.Thread(
                target=self._safe_build,
                args=(self.default,),
                daemon=True,
                name="index-build",
            )
            self._thread.start()

    def _safe_build(self, version: str) -> None:
        try:
            self.build(version)
        except Exception as e:  # recorded in state; tools report it
            self._record_failure(version, e)

    def _record_failure(self, version: str, e: Exception) -> None:
        st = self._states[version]
        st.phase, st.error, st.finished = (
            "error",
            f"{type(e).__name__}: {e}",
            time.time(),
        )
        self._ready[version].set()

    def build(self, version: str, force: bool | None = None) -> Index:
        """Open the cached index, rebuilding when stale. Thread-safe and idempotent."""
        force = self.cfg.force_rebuild if force is None else force
        with self._locks[version]:
            if version in self._indexes and not force:
                return self._indexes[version]
            info = self.versions[version]
            st = self._states[version]
            st.phase, st.started, st.error, st.done, st.total = (
                "scanning",
                time.time(),
                None,
                0,
                0,
            )
            if info.format not in ("oxygen", "legacy"):
                raise ValueError(f"{version}: unrecognised help format at {info.root}")

            def progress(done: int, total: int):
                st.phase, st.done, st.total = "parsing", done, total

            source = self._make_source(version, progress=progress)
            fp = db_fingerprint(source)
            dbp = self.db_path(version)
            fresh = False
            if dbp.exists() and not force:
                try:
                    idx = Index(dbp)
                    if idx._meta("fingerprint") == fp:
                        fresh = True
                    else:
                        idx.close()
                except sqlite3.DatabaseError:
                    pass
            if not fresh:
                st.phase = "parsing"
                stats = build_index(
                    source,
                    dbp,
                    progress=lambda ph, d, t: setattr(st, "phase", ph),
                    fingerprint=fp,
                )
                idx = Index(dbp)
                st.rebuilt, st.stats = True, stats
            else:
                st.rebuilt = False
                st.stats = json.loads(idx._meta("stats") or "{}")
            old = self._indexes.get(version)
            self._indexes[version] = idx
            if old:
                old.close()
            st.phase, st.finished = "ready", time.time()
            st.done = st.total = st.stats.get("pages", 0)
            self._ready[version].set()
            return idx

    def get(self, version: str | None = None, wait: float = 45.0) -> Index:
        if not self.versions:
            raise ValueError(
                "No Studio 5000 help folders found. Set S5K_HELP_ROOT to the "
                "'Studio 5000' install folder, the 'Logix Designer/ENU' folder, "
                "or an 'rs5000' help folder."
            )
        v = normalize_version(version) if version else self.default
        if v not in self.versions:
            raise ValueError(
                f"Unknown version {version!r}. Available: {', '.join(self.versions)}"
            )
        if v in self._indexes:
            return self._indexes[v]
        if v == self.default and self._thread is not None:
            if not self._ready[v].wait(wait):
                st = self._states[v]
                raise IndexNotReady(
                    f"The index for {v} is still building "
                    f"({st.phase}, {st.done}/{st.total} pages). "
                    "Retry in a few seconds."
                )
            st = self._states[v]
            if st.error:
                since = time.time() - st.finished
                if since < self.RETRY_AFTER:
                    raise RuntimeError(
                        f"Index build for {v} failed: {st.error} (will retry after "
                        f"{max(1, round(self.RETRY_AFTER - since))}s)"
                    )
                try:
                    return self.build(v)
                except Exception as e:
                    self._record_failure(v, e)
                    raise RuntimeError(
                        f"Index build for {v} failed again on retry: "
                        f"{type(e).__name__}: {e}"
                    ) from e
            if v not in self._indexes:  # another caller is retrying the build
                raise IndexNotReady(
                    f"The index for {v} is rebuilding ({st.phase}). "
                    "Retry in a few seconds."
                )
            return self._indexes[v]
        return self.build(v)
