"""Benchmark for comparing search backends.

Depends only on: studio5000_docs_mcp.config.Config and
studio5000_docs_mcp.index.IndexManager (.default, .build(version, force), and
Index.search(query, kind, section, limit) -> list of dicts with "path").

Usage:
    S5K_HELP_ROOT=".../Logix Designer/ENU" uv run python scripts/bench.py \
        [--holdout] [--verbose]
        [--root PATH] [--db-dir PATH] [--keep] [--model-dir PATH] [--json PATH]

The help folder is only read, never written. tests/eval_holdout.yaml is used only
with --holdout and must never be used for tuning. Exit code 2 when no help root is
given or found.

JSON schema (all times in seconds unless named *_ms, sizes in bytes unless named
*_mb):
{
  "meta": {"branch": str|null, "commit": str|null, "python": str, "platform": str,
           "version": str, "help_root": str, "db_dir": str},
  "cold_build_s": float,
  "warm_start_s": float,
  "index_size_bytes": int,
  "eval": {"<set name: eval|holdout>": {"file": str, "n": int, "hits_at_3": int,
                                          "hit_at_3": float,
                                          "mrr_at_10": float,
                                          "queries": [{"query": str, "rank": int|null,
                                                       "hit": bool}]}},
  "latency": {"n_queries": int, "runs_per_query": int, "median_ms": float,
              "p95_ms": float},
  "footprint": {"venv_mb": float, "model_cache_mb": float|null,
                "peak_rss_mb": float|null}
}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from studio5000_docs_mcp.config import Config
from studio5000_docs_mcp.index import IndexManager

TESTS = Path(__file__).resolve().parent.parent / "tests"
SETS = {"eval": "eval.yaml", "holdout": "eval_holdout.yaml"}
WARMUP = 5
RUNS = 3


def dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def peak_rss_mb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PMC),
                wintypes.DWORD,
            ]
            if not psapi.GetProcessMemoryInfo(
                k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb
            ):
                return None
            return pmc.PeakWorkingSetSize / 1e6
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return (
            rss / 1e6 if sys.platform == "darwin" else rss / 1e3
        )  # bytes on macOS, KB elsewhere
    except Exception:
        return None


def git_info() -> tuple[str | None, str | None]:
    def run(*args: str) -> str | None:
        try:
            r = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=Path(__file__).resolve().parent,
            )
            return r.stdout.strip() or None if r.returncode == 0 else None
        except Exception:
            return None

    return run("rev-parse", "--abbrev-ref", "HEAD"), run("rev-parse", "--short", "HEAD")


def run_eval(idx, items: list[dict]) -> dict:
    hits, rr, qs = 0, 0.0, []
    for item in items:
        res = idx.search(item["query"], kind=item.get("kind"), limit=10)
        paths = [r["path"] for r in res]
        exp = set(item["expected"])
        rank = next((i + 1 for i, p in enumerate(paths) if p in exp), None)
        ok = rank is not None and rank <= 3
        hits += ok
        rr += 1 / rank if rank else 0.0
        qs.append({"query": item["query"], "rank": rank, "hit": bool(ok)})
    n = len(items)
    return {
        "n": n,
        "hits_at_3": hits,
        "hit_at_3": hits / n if n else 0.0,
        "mrr_at_10": rr / n if n else 0.0,
        "queries": qs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="Help root (default: env S5K_HELP_ROOT)")
    ap.add_argument(
        "--db-dir",
        help="DB dir (default: fresh temp dir, removed at the end unless --keep)",
    )
    ap.add_argument("--keep", action="store_true", help="Keep the temp DB dir")
    ap.add_argument(
        "--holdout",
        action="store_true",
        help="Also run eval_holdout.yaml (reporting only; never tune against it)",
    )
    ap.add_argument("--verbose", action="store_true", help="Print per-query ranks")
    ap.add_argument(
        "--model-dir",
        help="Model cache dir to size (env FASTEMBED_CACHE_PATH / S5K_MODEL_DIR)",
    )
    ap.add_argument("--json", metavar="PATH", help="Write all numbers as JSON")
    a = ap.parse_args()

    root = a.root or os.environ.get("S5K_HELP_ROOT")
    if not root or not Path(root).expanduser().exists():
        print(
            "error: set S5K_HELP_ROOT (or --root) to the 'Logix Designer/ENU' "
            "folder or an rs5000 folder",
            file=sys.stderr,
        )
        return 2
    root = Path(root).expanduser()

    made_tmp = a.db_dir is None
    db_dir = (
        Path(tempfile.mkdtemp(prefix="s5k-bench-"))
        if made_tmp
        else Path(a.db_dir).expanduser()
    )
    db_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _run(a, root, db_dir)
    finally:
        if made_tmp and not a.keep:
            shutil.rmtree(db_dir, ignore_errors=True)


def _run(a, root: Path, db_dir: Path) -> int:
    if any(db_dir.iterdir()):
        print(
            f"error: db dir {db_dir} is not empty (cold build needs an empty dir)",
            file=sys.stderr,
        )
        return 2

    # 1. cold build
    mgr = IndexManager(Config(help_root=root, db_dir=db_dir, force_rebuild=True))
    if not mgr.default:
        print(f"error: no Oxygen help found under {root}", file=sys.stderr)
        return 2
    version = mgr.default
    t = time.perf_counter()
    mgr.build(version, force=True)
    cold = time.perf_counter() - t

    # 2. warm start (new manager, same db dir, no force)
    t = time.perf_counter()
    mgr2 = IndexManager(Config(help_root=root, db_dir=db_dir, force_rebuild=False))
    idx = mgr2.build(version, force=False)
    warm = time.perf_counter() - t

    # 3. size
    size = dir_size(db_dir)

    # 4. eval
    names = ["eval"] + (["holdout"] if a.holdout else [])
    items_by = {n: yaml.safe_load((TESTS / SETS[n]).read_text()) for n in names}
    evals = {}
    for n in names:
        r = run_eval(idx, items_by[n])
        evals[n] = {"file": SETS[n], **r}

    # 5. latency
    queries = [(i["query"], i.get("kind")) for n in names for i in items_by[n]]
    for k in range(WARMUP):
        q, kind = queries[k % len(queries)]
        idx.search(q, kind=kind, limit=10)
    times = []
    for _ in range(RUNS):
        for q, kind in queries:
            t = time.perf_counter()
            idx.search(q, kind=kind, limit=10)
            times.append((time.perf_counter() - t) * 1000)
    times.sort()
    p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
    latency = {
        "n_queries": len(queries),
        "runs_per_query": RUNS,
        "median_ms": statistics.median(times),
        "p95_ms": p95,
    }

    # 6. footprint
    model_dir = (
        a.model_dir
        or os.environ.get("FASTEMBED_CACHE_PATH")
        or os.environ.get("S5K_MODEL_DIR")
    )
    model_mb = (
        dir_size(Path(model_dir).expanduser()) / 1e6
        if model_dir and Path(model_dir).expanduser().is_dir()
        else None
    )
    foot = {
        "venv_mb": dir_size(Path(sys.prefix)) / 1e6,
        "model_cache_mb": model_mb,
        "peak_rss_mb": peak_rss_mb(),
    }

    # 7. meta
    branch, commit = git_info()
    meta = {
        "branch": branch,
        "commit": commit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "version": version,
        "help_root": str(root),
        "db_dir": str(db_dir),
    }

    out = {
        "meta": meta,
        "cold_build_s": cold,
        "warm_start_s": warm,
        "index_size_bytes": size,
        "eval": evals,
        "latency": latency,
        "footprint": foot,
    }

    def f(v, fmt="{:.1f}"):
        return "n/a" if v is None else fmt.format(v)

    print(f"Branch/commit : {branch} @ {commit}")
    print(f"Python        : {meta['python']}  ({meta['platform']})")
    print(f"Help version  : {version}  root={root}")
    print("-" * 60)
    print(f"Cold build    : {cold:.1f} s   (OS file cache may be warm)")
    print(f"Warm start    : {warm:.2f} s")
    print(f"Index size    : {size / 1e6:.1f} MB")
    for n in names:
        e = evals[n]
        print("-" * 60)
        print(
            f"[{n}: {e['file']}]  "
            f"hit@3 = {e['hits_at_3']}/{e['n']} = {e['hit_at_3']:.2f}   "
            f"MRR@10 = {e['mrr_at_10']:.3f}"
        )
        if a.verbose:
            for q in e["queries"]:
                print(
                    f"  {'HIT ' if q['hit'] else 'MISS'} rank={q['rank']}  "
                    f"{q['query']!r}"
                )
    print("-" * 60)
    print(
        f"Latency       : median {latency['median_ms']:.1f} ms, "
        f"p95 {latency['p95_ms']:.1f} ms  "
        f"({len(queries)} queries x {RUNS}, after {WARMUP} warm-up)"
    )
    print(f"Venv          : {foot['venv_mb']:.0f} MB")
    print(f"Model cache   : {f(model_mb, '{:.0f} MB')}")
    print(f"Peak RSS      : {f(foot['peak_rss_mb'], '{:.0f} MB')}")
    if a.holdout:
        print(
            "Reminder      : the holdout set is for reporting only; "
            "never tune against it."
        )

    if a.json:
        Path(a.json).expanduser().write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
