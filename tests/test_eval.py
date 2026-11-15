from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

HERE = Path(__file__).parent
# name -> (file, hit@3 floor). eval.yaml is the tuning set; eval_holdout.yaml must
# never be tuned against.
SETS = {
    "eval": ("eval.yaml", 0.7),
    "holdout": ("eval_holdout.yaml", 0.5),
}


@pytest.mark.parametrize("name", list(SETS))
def test_hit_at_3(index, name):
    filename, threshold = SETS[name]
    items = yaml.safe_load((HERE / filename).read_text())
    hits = 0
    rr = 0.0
    lines = []
    for item in items:
        res = index.search(item["query"], kind=item.get("kind"), limit=10)
        paths = [r["path"] for r in res]
        exp = set(item["expected"])
        rank = next((i + 1 for i, p in enumerate(paths) if p in exp), None)
        ok = rank is not None and rank <= 3
        hits += ok
        rr += 1 / rank if rank else 0.0
        lines.append(f"{'HIT ' if ok else 'MISS'} rank={rank}  {item['query']!r}")
        if not ok:
            lines += [f"       got: {r['title']} [{r['path']}]" for r in res[:3]]
    score = hits / len(items)
    print(f"\n[{name}: {filename}]")
    print("\n".join(lines))
    print(
        f"{name} hit@3 = {hits}/{len(items)} = {score:.2f}   "
        f"MRR@10 = {rr / len(items):.3f}"
    )
    assert score >= threshold
