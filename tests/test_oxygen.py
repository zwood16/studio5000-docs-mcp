from studio5000_docs_mcp.sources.oxygen import OxygenSource, is_oxygen_root


def test_is_oxygen_root(synthetic_root, tmp_path):
    assert is_oxygen_root(synthetic_root)
    assert not is_oxygen_root(tmp_path)


def test_toc_loads_child_file_despite_has_children_false(synthetic_root):
    src = OxygenSource(synthetic_root, "v99")
    nodes = src.toc()
    assert [(n.node_id, n.parent_id, n.title, n.href) for n in nodes] == [
        ("ait_1-d1", None, "Widget Overview", "help/a.html"),
        ("ait_2-d2", "ait_1-d1", "Widget Counter (WCT)", "help/clinset/wct.html"),
    ]


def test_toc_title_strips_html(synthetic_root):
    assert OxygenSource(synthetic_root, "v99").toc_title() == "Synthetic Help"


def test_toc_missing_returns_empty(tmp_path):
    (tmp_path / "oxygen-webhelp").mkdir()
    assert OxygenSource(tmp_path, "v99").toc() == []


def test_help_ids(synthetic_root):
    ids = OxygenSource(synthetic_root, "v99").help_ids()
    assert ("123", "help/clinset/wct.html") in ids  # from the xml
    assert ("456", "help/a.html") in ids  # from the csv (no header)
    assert ("789", "help/csvonly.html") in ids
    assert all(h for h, _ in ids)  # empty helpID skipped
    assert len(ids) == 3


def test_csv_toc_paths(synthetic_root):
    paths = OxygenSource(synthetic_root, "v99").csv_toc_paths()
    assert paths["help/a.html"] == [
        "Top Section",
        "Sub Section",
    ]  # page dropped, ditamap root dropped


def test_keywords(synthetic_root):
    assert OxygenSource(synthetic_root, "v99").keywords() == {
        "help/a.html": ["gadgets frobnicate"]
    }


def test_pages_and_fingerprint(synthetic_root):
    src = OxygenSource(synthetic_root, "v99")
    assert src.page_count() == 4
    assert src.fingerprint().startswith("oxygen:4:")
    pages = {
        p.path: p for p in src.iter_pages()
    }  # small tree: serial path, no process pool
    assert set(pages) == {
        "help/a.html",
        "help/clinset/wct.html",
        "help/orphan.html",
        "help/csvonly.html",
    }
    assert pages["help/clinset/wct.html"].relation == "help/a.html"
