"""Accuracy of the page `kind` classifier against hand-labelled pages (tests/kind_labels.yaml).

Definitions used for labelling:
  instruction: documents specific programming instructions (mnemonic); category overviews are concept
  fault:       fault/error codes, error/warning/status messages ("Failed to ..."), module errors
  command:     a menu/toolbar/context-menu command
  dialog:      a dialog box, tab, wizard page, property page or IDE window/pane
  module:      I/O or communication module specifics (config, wiring, specs, module-defined data), not faults
  concept:     everything else (overviews, procedures, explanations)
"""
from collections import Counter
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

LABELS = yaml.safe_load((Path(__file__).parent / "kind_labels.yaml").read_text())


def _score(index, entries, name):
    wrong = []
    for e in entries:
        page = index.resolve_page(e["path"])
        assert page, f"labelled page missing from index: {e['path']}"
        if page["kind"] != e["kind"]:
            wrong.append((e["kind"], page["kind"], page["title"]))
    acc = 1 - len(wrong) / len(entries)
    print(f"\nkind accuracy [{name}] = {len(entries) - len(wrong)}/{len(entries)} = {acc:.2f}")
    print("  confusion (label -> predicted):", dict(Counter((a, b) for a, b, _ in wrong)))
    for a, b, t in wrong:
        print(f"  MISS label={a} got={b}: {t}")
    return acc


def test_kind_tuning_set(index):
    assert _score(index, LABELS["tuning"], "tuning") >= 0.85


def test_kind_holdout_set(index):
    assert _score(index, LABELS["holdout"], "holdout") >= 0.75
