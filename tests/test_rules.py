import pytest

from studio5000_docs_mcp.index import classify, extract_mnemonics, section_of

CLINSET = "help/clinset/x/y.html"


def test_extract_mnemonics_parenthesised():
    assert extract_mnemonics("Timer On Delay (TON)") == ["TON"]
    assert extract_mnemonics("Get System Value (GSV) and Set System Value (SSV)") == ["GSV", "SSV"]
    assert extract_mnemonics("Same (TON) and again (TON)") == ["TON"]


def test_extract_mnemonics_rejects():
    assert extract_mnemonics("Options command") == []
    assert extract_mnemonics("Something (Optional)") == []
    assert extract_mnemonics("Something (A)") == []  # too short
    assert extract_mnemonics("Something (123)") == []  # no letter


def test_extract_mnemonics_dash_form_only_in_clinset():
    assert extract_mnemonics("DINT to String-DTOS", "help/clinset/conv/dtos.html") == ["DTOS"]
    assert extract_mnemonics("DINT to String-DTOS", "help/other/dtos.html") == []
    assert extract_mnemonics("DINT to String-DTOS") == []


@pytest.mark.parametrize("path,title,mn,kind", [
    (CLINSET, "Timer On Delay (TON)", "TON", "instruction"),
    (CLINSET, "Some overview", "", "concept"),
    ("help/modules/m.html", "Module Info", "", "module"),
    ("help/modules/module-errors/e.html", "Whatever", "", "fault"),
    ("help/a/b.html", "Major Faults", "", "fault"),
    ("help/a/b.html", "Connection error codes", "", "fault"),
    ("help/a/b.html", "Options command", "", "command"),
    ("help/a/save-command.html", "Save", "", "command"),
    ("help/a/b.html", "Controller Properties dialog box", "", "dialog"),
    ("help/a/b.html", "General tab", "", "dialog"),
    ("help/a/b.html", "Getting started", "", "concept"),
    (CLINSET, "Incremental Mode Flow Chart-FSC", "FSC", "concept"),
    ("help/a/b.html", "Failed to delete component", "", "fault"),
    ("help/a/b.html", "Unable to download to controller.", "", "fault"),
    ("help/a/b.html", "Error - Failed to retrieve information", "", "fault"),
    ("help/a/b.html", "Controller Properties dialog box - Major Faults tab", "", "dialog"),
    ("help/modules/m.html", "Configuration Tab 1756-IF6I and -IF6CIS", "", "dialog"),
    ("help/a/b.html", "Module Properties - General for 1756-IF16", "", "dialog"),
    ("help/a/b.html", "Edit routine properties", "", "concept"),
    ("help/a/b.html", "Clear Axis Faults command", "", "command"),
    ("help/a/b.html", "Create-Configure Module 1734-AENT", "", "module"),
    ("help/modules/w/x.html", "Wiring Diagram - 1756-IF16", "", "module"),
])
def test_classify(path, title, mn, kind):
    assert classify(path, title, mn) == kind


def test_section_of():
    assert section_of("help/clinset/timer/ton.html") == "help/clinset"
    assert section_of("publications/guide/x.html") == "publications/guide"
    assert section_of("help/x.html") == "help"
    assert section_of("common-content/a/b.html") == "common-content"
