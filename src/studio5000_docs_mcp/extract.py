"""Oxygen WebHelp <article> -> Markdown, plus text helpers for FTS."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote

from selectolax.lexbor import LexborHTMLParser, LexborNode

SKIP_TAGS = {
    "script",
    "style",
    "nav",
    "noscript",
    "template",
    "head",
    "svg",
    "button",
    "form",
}
SKIP_CLASS_PARTS = ("related-links", "linklist", "note__title")
INLINE_TAGS = {
    "a",
    "span",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "code",
    "tt",
    "kbd",
    "samp",
    "var",
    "sup",
    "sub",
    "br",
    "img",
    "small",
    "big",
    "abbr",
    "cite",
    "q",
    "mark",
    "s",
    "del",
    "ins",
    "bdo",
    "wbr",
    "label",
}
_WS = re.compile(r"\s+")
# legacy (v31-v36) help classes; Oxygen pages never use these
_LEGACY_DROP_TABLES = {"relatedtopics", "tableindexlinks", "tableindexatoz"}
_LEGACY_LABELS = {
    "NOTE",
    "TIP",
    "IMPORTANT",
    "EXAMPLE",
    "WARNING",
    "ATTENTION",
    "CAUTION",
    "SAFETY ADVISORY",
}
_LEGACY_LABEL_ALIASES = {
    "SAFETY ADVISORY GRAPHIC": "SAFETY ADVISORY",
    "SM_SAFETY_ADVISORY": "SAFETY ADVISORY",
}
_LEGACY_CODE = {"codeindent1", "codeindent2", "codeindent3"}


def _cls(node: LexborNode) -> str:
    return node.attributes.get("class") or ""


def resolve_ref(page_path: str, href: str) -> str | None:
    """Resolve a relative href to a help-root-relative path. None if not local."""
    href = href.strip().replace("\\", "/")
    if not href or href.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
        return None
    href = href.split("#", 1)[0].split("?", 1)[0]
    if not href:
        return None
    href = unquote(href)
    p = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), href))
    if p.startswith("..") or p.startswith("/"):
        return None
    return p


class _Converter:
    def __init__(self, page_path: str, legacy: bool = False):
        self.page_path = page_path
        self.legacy = legacy  # enables legacy (v31-v36) class handling
        self.links: list[str] = []

    # ---------- inline ----------
    def inline(self, node: LexborNode) -> str:
        out: list[str] = []
        for ch in node.iter(include_text=True):
            out.append(self._inline_node(ch))
        return "".join(out)

    def _inline_node(self, n: LexborNode) -> str:
        tag = n.tag
        if tag == "-text":
            return _WS.sub(" ", n.text_content or "")
        if tag == "-comment":
            return ""
        if tag in SKIP_TAGS or any(p in _cls(n) for p in SKIP_CLASS_PARTS):
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            src = n.attributes.get("src") or ""
            alt = (n.attributes.get("alt") or "").strip()
            if not src:
                return ""
            p = resolve_ref(self.page_path, src)
            if p is None:
                return ""
            return f"![{alt}]({p})"
        if tag == "a":
            inner = self.inline(n)
            href = n.attributes.get("href") or ""
            text = inner.strip()
            if not text:
                return inner
            if re.match(r"^https?://", href.strip(), re.I):
                return f"[{text}]({href.strip()})"
            p = resolve_ref(self.page_path, href)
            if p is None or not p.endswith((".html", ".htm")):
                return inner
            if p != self.page_path and p not in self.links:
                self.links.append(p)
            text = text.replace("[", "(").replace("]", ")")
            lead = " " if inner[:1].isspace() else ""
            trail = " " if inner[-1:].isspace() else ""
            return f"{lead}[{text}](page:{p}){trail}"
        inner = self.inline(n)
        if tag in ("strong", "b", "em", "i"):
            m = re.match(r"^(\s*)(.*?)(\s*)$", inner, re.S)
            core = m.group(2)
            if not core:
                return inner
            mark = "**" if tag in ("strong", "b") else "*"
            return f"{m.group(1)}{mark}{core}{mark}{m.group(3)}"
        if tag in ("code", "tt", "kbd", "samp"):
            core = inner.strip()
            return f"`{core}`" if core and "`" not in core else inner
        if tag == "sup":
            return f"^{inner.strip()}" if inner.strip() else ""
        if tag == "sub":
            return f"_{inner.strip()}" if inner.strip() else ""
        return inner

    @staticmethod
    def _tidy(text: str) -> str:
        lines = [ln.strip() for ln in text.split("\n")]
        return "\n".join(ln for ln in lines).strip()

    def _para(self, parts: list[LexborNode]) -> str:
        return self._tidy("".join(self._inline_node(p) for p in parts))

    # ---------- blocks ----------
    def blocks(self, node: LexborNode) -> list[str]:
        out: list[str] = []
        run: list[LexborNode] = []

        def flush():
            if run:
                t = self._para(run)
                if t:
                    out.append(t)
                run.clear()

        for ch in node.iter(include_text=True):
            tag = ch.tag
            if tag == "-text":
                run.append(ch)
                continue
            if tag == "-comment":
                continue
            if tag in SKIP_TAGS or any(p in _cls(ch) for p in SKIP_CLASS_PARTS):
                continue
            if tag in INLINE_TAGS:
                run.append(ch)
                continue
            flush()
            out.extend(self._block(ch))
        flush()
        return out

    def _block(self, n: LexborNode) -> list[str]:
        tag = n.tag
        cls = _cls(n)
        if tag == "p":
            t = self._tidy(self.inline(n))
            if not t:
                return []
            classes = cls.split()
            if self.legacy and "keywords" in classes:  # legacy index keywords
                return []
            if "subheading" in classes or (self.legacy and "subheading2" in classes):
                return [f"### {t.replace(chr(10), ' ')}"]
            if self.legacy and any(c in _LEGACY_CODE for c in classes) and "`" not in t:
                return ["\n".join(f"`{ln}`" if ln else "" for ln in t.split("\n"))]
            return [t]
        if tag == "h1":
            return []
        if re.fullmatch(r"h[2-6]", tag):
            t = self._tidy(self.inline(n)).replace("\n", " ")
            return [f"{'#' * max(2, int(tag[1]))} {t}"] if t else []
        if tag in ("ul", "ol"):
            b = self._list(n)
            return [b] if b else []
        if tag == "table":
            classes = set(cls.split())
            if self.legacy and classes & _LEGACY_DROP_TABLES:  # nav/index tables
                return []
            if self.legacy and "tableintopic" in classes:
                note_rows = self._legacy_note_rows(n)
                if note_rows:
                    return self._legacy_note(note_rows)
            b = self._table(n)
            return [b] if b else []
        if tag == "pre":
            code = n.text(deep=True, strip=False).strip("\n")
            return [f"```\n{code}\n```"] if code.strip() else []
        if tag == "div" and re.search(r"\bnote\b", cls):
            return self._note(n)
        if tag == "li":
            return self.blocks(n)
        if tag in ("dt",):
            t = self._tidy(self.inline(n))
            return [f"**{t}**"] if t else []
        if tag == "hr":
            return []
        return self.blocks(n)

    def _note(self, n: LexborNode) -> list[str]:
        title_node = n.css_first("span.note__title")
        title = self._tidy(self.inline(title_node)) if title_node else ""
        inner = self.blocks(n)
        if title and inner:
            inner[0] = f"**{title}** {inner[0]}"
        elif title:
            inner = [f"**{title}**"]
        if not inner:
            return []
        text = "\n\n".join(inner)
        return ["\n".join(f"> {ln}" if ln else ">" for ln in text.split("\n"))]

    @staticmethod
    def _has_content(cell: LexborNode) -> bool:
        return bool(cell.text(strip=True)) or cell.css_first("img") is not None

    @staticmethod
    def _note_label(raw: str) -> str:
        """Normalise a candidate note label; "" when it is not an allowed label."""
        t = _WS.sub(" ", raw).strip().rstrip(":").strip().upper()
        t = _LEGACY_LABEL_ALIASES.get(t, t)
        return t if t in _LEGACY_LABELS else ""

    @staticmethod
    def _legacy_note_rows(table: LexborNode) -> list[tuple[str, list[LexborNode]]]:
        """(label, body cells) per note row, or [] when the table is not a note table.

        Spacer rows (no text, no img) are skipped; every other row must be a note row.
        A note row's first cell gives an allowed label via p.tableheadingright, a
        p.tableheading cell whose whole text is the label, or an image-only cell whose
        alt is the label; a later cell must have content. Icon legends stay tables.
        """
        out: list[tuple[str, list[LexborNode]]] = []
        for tr, _ in _Converter._rows(table):
            cells = [c for c in tr.iter() if c.tag in ("td", "th")]
            if not any(_Converter._has_content(c) for c in cells):
                continue
            first = cells[0]
            text = first.text(strip=True)
            head = first.css_first("p.tableheadingright")
            img = first.css_first("img")
            label = ""
            if head is not None:
                label = _Converter._note_label(head.text(strip=True))
            elif first.css_first("p.tableheading") is not None and text:
                label = _Converter._note_label(text)
            elif img is not None and not text:
                label = _Converter._note_label(img.attributes.get("alt") or "")
            body = [c for c in cells[1:] if _Converter._has_content(c)]
            if not label or not body:
                return []
            out.append((label, body))
        return out

    def _legacy_note(self, rows: list[tuple[str, list[LexborNode]]]) -> list[str]:
        result: list[str] = []
        for label, body in rows:
            inner: list[str] = []
            for c in body:
                inner.extend(self.blocks(c))
            if label and inner:
                inner[0] = f"**{label}** {inner[0]}"
            elif label:
                inner = [f"**{label}**"]
            if not inner:
                continue
            text = "\n\n".join(inner)
            result.append(
                "\n".join(f"> {ln}" if ln else ">" for ln in text.split("\n"))
            )
        return result

    def _list(self, n: LexborNode) -> str:
        lines: list[str] = []
        ordered = n.tag == "ol"
        i = 0
        for li in n.iter():
            if li.tag != "li":
                continue
            i += 1
            marker = f"{i}. " if ordered else "- "
            blks = self.blocks(li)
            if not blks:
                continue
            body: list[str] = []
            for b in blks:
                body.extend(b.split("\n"))
            indent = " " * len(marker)
            lines.append(marker + body[0])
            lines.extend((indent + ln) if ln else "" for ln in body[1:])
        return "\n".join(lines)

    # ---------- tables ----------
    @staticmethod
    def _rows(table: LexborNode) -> list[tuple[LexborNode, bool]]:
        rows: list[tuple[LexborNode, bool]] = []
        for ch in table.iter():
            if ch.tag in ("thead", "tbody", "tfoot"):
                for tr in ch.iter():
                    if tr.tag == "tr":
                        rows.append((tr, ch.tag == "thead"))
            elif ch.tag == "tr":
                rows.append((ch, False))
        return rows

    def _cell_text(self, cell: LexborNode) -> str:
        blks = self.blocks(cell)
        lines: list[str] = []
        for b in blks:
            lines.extend(ln.strip() for ln in b.split("\n") if ln.strip())
        return "<br>".join(lines).replace("|", "\\|")

    def _table(self, table: LexborNode) -> str:
        rows = self._rows(table)
        if not rows:
            return ""
        grid: list[list[str]] = []
        pending: dict[tuple[int, int], str] = {}  # (row, col) -> text from rowspan
        header_rows = 0
        for r, (tr, in_thead) in enumerate(rows):
            line: list[str] = []
            c = 0
            cells = [x for x in tr.iter() if x.tag in ("td", "th")]
            all_th = bool(cells) and all(x.tag == "th" for x in cells)
            if (in_thead or all_th) and header_rows == r:
                header_rows += 1
            for cell in cells:
                while (r, c) in pending:
                    line.append(pending.pop((r, c)))
                    c += 1
                text = self._cell_text(cell)
                try:
                    cs = max(1, int(cell.attributes.get("colspan") or 1))
                except ValueError:
                    cs = 1
                try:
                    rs = max(1, int(cell.attributes.get("rowspan") or 1))
                except ValueError:
                    rs = 1
                cs = min(cs, 50)
                rs = min(rs, 200)
                for k in range(cs):
                    line.append(text if k == 0 else "")
                    for j in range(1, rs):
                        pending[(r + j, c + k)] = text
                c += cs
            while (r, c) in pending:
                line.append(pending.pop((r, c)))
                c += 1
            grid.append(line)
        ncols = max((len(x) for x in grid), default=0)
        if ncols == 0:
            return ""
        grid = [x + [""] * (ncols - len(x)) for x in grid]
        grid = [x for x in grid if any(cell for cell in x)] or []
        if not grid:
            return ""
        # header: use leading header rows, collapse to one by joining; else first row
        hdr = grid[0]
        body = grid[1:]
        out = []
        cap = table.css_first("caption")
        cap_t = self._tidy(self.inline(cap)).replace("\n", " ") if cap else ""
        if cap_t:
            out.append(f"**{cap_t}**\n")
        out.append("| " + " | ".join(hdr) + " |")
        out.append("|" + "|".join([" --- "] * ncols) + "|")
        for row in body:
            out.append("| " + " | ".join(row) + " |")
        return "\n".join(out)


def convert_article(
    article: LexborNode, page_path: str, legacy: bool = False
) -> tuple[str, list[str]]:
    conv = _Converter(page_path, legacy)
    blocks = conv.blocks(article)
    md = "\n\n".join(blocks)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md, conv.links


def _meta(tree: LexborHTMLParser, name: str) -> str:
    n = tree.css_first(f'meta[name="{name}"]')
    return (n.attributes.get("content") or "").strip() if n else ""


def parse_html(html: str, fallback_path: str):
    """Parse one Oxygen page. Returns a RawPage."""
    from .sources.base import RawPage

    tree = LexborHTMLParser(html)
    path = _meta(tree, "wh-out-relpath").replace("\\", "/") or fallback_path
    t = tree.css_first("head > title") or tree.css_first("title")
    title = _WS.sub(" ", t.text(strip=True)).strip() if t else ""
    article = (
        tree.css_first("main article")
        or tree.css_first("article")
        or tree.css_first("main")
    )
    h1 = article.css_first("h1") if article is not None else None
    if not title and h1 is not None:
        title = _WS.sub(" ", h1.text(strip=True))
    if article is not None:
        md, links = convert_article(article, path)
    else:
        md, links = "", []
    # the article h1 is skipped by the converter; prepend the canonical title heading
    md = (f"# {title}\n\n{md}" if title else md).strip()
    rel = _meta(tree, "DC.relation").replace("\\", "/")
    rel_path = resolve_ref(path, rel) if rel else ""
    return RawPage(
        path=path,
        title=title,
        description=_meta(tree, "description"),
        identifier=_meta(tree, "DC.identifier"),
        toc_id=_meta(tree, "wh-toc-id"),
        relation=rel_path or "",
        markdown=md,
        links=links,
    )


def parse_legacy_html(html: str, path: str):
    """Parse one legacy (v31-v36) page. Returns a RawPage."""
    from .sources.base import RawPage

    tree = LexborHTMLParser(html)
    section = tree.css_first("#content_section")
    t = tree.css_first("head > title") or tree.css_first("title")
    title = _WS.sub(" ", t.text(strip=True)).strip() if t else ""
    if not title and section is not None:
        h1 = section.css_first("h1")
        if h1 is not None:
            title = _WS.sub(" ", h1.text(strip=True)).strip()
    if section is not None:
        md, links = convert_article(section, path, legacy=True)
    else:
        md, links = "", []
    md = (f"# {title}\n\n{md}" if title else md).strip()
    crumbs: list[tuple[str, str]] = []
    for a in tree.css("p.breadcrumbs a.breadcrumblink"):
        ref = resolve_ref(path, a.attributes.get("href") or "")
        text = _WS.sub(" ", a.text(strip=True)).strip()
        if ref and text:
            crumbs.append((text, ref))
    return RawPage(path=path, title=title, markdown=md, links=links, crumb_trail=crumbs)


# ---------------- text helpers for FTS ----------------

_LINK = re.compile(r"!?\[([^\]]*)\]\((?:page:|https?:)[^)]*\)")
_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_TRAIL = re.compile(r"(?<=\w)[.\-_]+(?!\w)")
_TOKEN = re.compile(r"[\w.\-]+")


def markdown_to_text(md: str) -> str:
    t = _IMG.sub(" ", md)
    t = _LINK.sub(r"\1", t)
    t = re.sub(r"^\s*(?:#+\s+|>\s?|[-*]\s+|\d+\.\s+)", "", t, flags=re.M)
    t = t.replace("<br>", " ").replace("```", " ").replace("|", " ").replace("**", "")
    t = re.sub(r"^\s*(?:-{3,}|:?-{3,}:?)\s*$", " ", t, flags=re.M)
    return clean_punct(t)


def clean_punct(text: str) -> str:
    """Remove sentence-final '.', '-', '_' glued to words.

    (The tokenchars setting would otherwise keep them.)"""
    return _TRAIL.sub("", text)


def expand_identifiers(text: str) -> str:
    """Extra tokens so parts of Timer.DN, 1756-IF16, Axis_CIP_Drive are searchable."""
    extra: set[str] = set()
    for tok in _TOKEN.findall(text):
        tok = (
            tok.strip("-_.") if not tok.startswith(".") else "." + tok[1:].strip("-_.")
        )
        if not tok or not any(c in tok for c in "._-"):
            continue
        low = tok.lower()
        if (
            low.startswith(".")
            and len(low) > 1
            and not any(c in low[1:] for c in "._-")
        ):
            extra.add(low[1:])
            continue
        parts = [p for p in re.split(r"[._\-]+", low) if p]
        if len(parts) < 2 and not low.startswith("."):
            continue
        for i, p in enumerate(parts):
            extra.add(p)
            if "." in low and i > 0:
                extra.add("." + p)
    return " ".join(sorted(extra))


_STOP = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    ]
    + ["between", "difference", "use", "using", "my", "so"]
)


def query_tokens(query: str) -> list[str]:
    """Sanitised tokens safe to quote into an FTS5 query."""
    toks = []
    for raw in _TOKEN.findall(query):
        lead_dot = raw.startswith(".")
        t = raw.strip("-_.")
        if not t:
            continue
        if lead_dot:
            t = "." + t
        toks.append(t)
    return toks


def is_stop(tok: str) -> bool:
    """Common English words are dropped, except ALL-CAPS ones.

    (FOR, AND, OR, NOT are mnemonics.)"""
    return tok.lower() in _STOP and not (tok.isupper() and len(tok) > 1)


# Query-side synonyms: general Logix/PLC vocabulary equivalences only (keyed by
# lower-case word, applied to lower/mixed-case query words, never to ALL-CAPS
# mnemonics). Each entry must be an equivalence a controls engineer would use in any
# context. Do NOT add entries whose purpose is to steer one specific query to its
# expected page, and no query-specific phrases. Expansions only widen candidate
# recall; they never count toward title coverage. Values are single FTS tokens or
# phrases (hyphenated words are one token in this index, hence "one-shot").
SYNONYMS: dict[str, tuple[str, ...]] = {
    "create": ("define", "new"),
    "make": ("create",),
    "check": ("examine", "test"),
    "once": ("one-shot",),
    "used": ("reference", "cross-reference"),
    "usage": ("reference", "cross-reference"),
    "delete": ("remove",),
    "remove": ("delete",),
    "equal": ("compare",),
    "equality": ("compare",),
    "integer": ("dint", "int"),
    "scope": ("scoped",),
    "scoped": ("scope",),
    "latch": ("set",),
    "unlatch": ("reset",),
    "online": ("go online",),
    "offline": ("go offline",),
    "variable": ("tag",),
    "find": ("search",),
    "search": ("find",),
    "change": ("modify",),
    "modify": ("change",),
    "show": ("display",),
    "display": ("show",),
    "error": ("fault",),
    "fault": ("error",),
    "controller": ("processor",),
    "delay": ("timer",),
}

_PLAIN_HYPHEN = re.compile(r"^[A-Za-z]+(?:-[A-Za-z]+)+$")


def _q(t: str) -> str:
    return f'"{t.replace('"', "")}"'


def query_term(tok: str) -> tuple[str, str]:
    """(original, expanded) FTS5 expressions for one query token from query_tokens().

    original matches the token as typed; a plain-word hyphenated token
    ('program-scoped') also matches its parts, mirroring expand_identifiers at index
    time. Catalog-style tokens ('1756-IF16') stay whole. expanded adds synonyms
    (lower/mixed case only). Both are valid FTS5 and always parenthesised or
    quoted."""
    if tok.isupper() and len(tok) > 1:
        return _q(tok), _q(tok)
    orig = _q(tok)
    if _PLAIN_HYPHEN.match(tok):
        parts = [p for p in tok.lower().split("-") if not is_stop(p)]
        if len(parts) >= 2:
            orig = f"({_q(tok)} OR ({' AND '.join(_q(p) for p in parts)}))"
        elif len(parts) == 1:
            orig = f"({_q(tok)} OR {_q(parts[0])})"
    low = tok.lower()
    syns = SYNONYMS.get(low) or SYNONYMS.get(low[:-1] if low.endswith("s") else low, ())
    if not syns:
        return orig, orig
    return orig, f"({orig} OR {' OR '.join(_q(s) for s in syns)})"
