"""Оценка ретривала по golden set: precision@k, MRR (и сверка keywords).

Golden set — agent-memory/golden_set.json: размеченные запросы
с expected_procedure_ids. Наполняется по мере появления процедур.

Использование:
  python scripts/eval-retrieval.py
  python scripts/eval-retrieval.py --top 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import MEMORY_DIR, read_json  # noqa: E402
from retrieve import tokenize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=5, help="k для precision@k / MRR")
    args = ap.parse_args()

    golden = read_json(MEMORY_DIR / "golden_set.json")
    queries = golden.get("queries", [])
    if not queries:
        print("Golden set пуст.")
        return 0

    from retrieve import retrieve  # noqa: PLC0415

    precision_sum, mrr_sum, counted = 0.0, 0.0, 0
    print(f"{'id':<6} {'precision@'+str(args.top):<16} {'MRR':<6} запрос")
    for q in queries:
        res = retrieve(q["query"], top_k=args.top)
        top_ids = [c["id"] for c in res["candidates"]]
        expected = set(q.get("expected_procedure_ids", []))
        hits = sum(1 for pid in top_ids if pid in expected)
        k = max(len(top_ids), 1)
        precision = hits / k if expected else None
        mrr = 0.0
        for rank, pid in enumerate(top_ids, 1):
            if pid in expected:
                mrr = 1.0 / rank
                break
        if expected:
            precision_sum += precision or 0.0
            mrr_sum += mrr
            counted += 1
        p_str = f"{precision:.3f}" if precision is not None else "n/a (нет expected)"
        print(f"{q['id']:<6} {p_str:<16} {mrr:.3f} {q['query']}")
        if top_ids:
            print(f"       top: {', '.join(top_ids)}")

    if counted:
        print(f"\nСреднее по {counted} размеченным запросам: "
              f"precision@{args.top} = {precision_sum/counted:.3f}, "
              f"MRR = {mrr_sum/counted:.3f}")
    else:
        print("\nНи один запрос не размечен (expected_procedure_ids пуст) — "
              "заполняй golden set по мере появления процедур.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())