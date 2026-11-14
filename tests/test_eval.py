from pathlib import Path

import yaml

EVAL = yaml.safe_load((Path(__file__).parent / "eval.yaml").read_text())
THRESHOLD = 0.7


def test_hit_at_3(index):
    hits = 0
    lines = []
    for item in EVAL:
        res = index.search(item["query"], kind=item.get("kind"), limit=10)
        paths = [r["path"] for r in res]
        exp = set(item["expected"])
        rank = next((i + 1 for i, p in enumerate(paths) if p in exp), None)
        ok = rank is not None and rank <= 3
        hits += ok
        lines.append(f"{'HIT ' if ok else 'MISS'} rank={rank}  {item['query']!r}")
        if not ok:
            lines += [f"       got: {r['title']} [{r['path']}]" for r in res[:3]]
    score = hits / len(EVAL)
    print("\n".join(lines))
    print(f"hit@3 = {hits}/{len(EVAL)} = {score:.2f}")
    assert score >= THRESHOLD
