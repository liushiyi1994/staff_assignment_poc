"""Run the eval: capgraph + baselines over briefs.jsonl -> data/eval/results.md.

Metrics (implemented + unit-tested here): Recall@k, MRR. For capgraph, a brief's ranking
is the flattened first-role shortlist (extend to multi-role later if briefs warrant it).

Cost note: capgraph eval calls the LLM (intent + rerank) per brief — with 150 briefs this
is the most expensive query-side step; budget-guarded by llm.max_stage_cost_usd.
"""
from __future__ import annotations

from ..models import EvalBrief, EvalResult
from ..settings import DATA_DIR, settings

BRIEFS_PATH = DATA_DIR / "eval" / "briefs.jsonl"
RESULTS_PATH = DATA_DIR / "eval" / "results.md"


def recall_at_k(ranked: list[str], truth: set[str], k: int) -> float:
    return 1.0 if truth & set(ranked[:k]) else 0.0


def mrr(ranked: list[str], truth: set[str]) -> float:
    for i, pid in enumerate(ranked, 1):
        if pid in truth:
            return 1.0 / i
    return 0.0


def evaluate(system: str, rank_fn, briefs: list[EvalBrief]) -> EvalResult:
    ks = settings["eval.recall_at"]
    rec = {k: 0.0 for k in ks}
    mrr_total = 0.0
    for b in briefs:
        ranked = rank_fn(b.text)
        truth = set(b.true_person_ids)
        for k in ks:
            rec[k] += recall_at_k(ranked, truth, k)
        mrr_total += mrr(ranked, truth)
    n = len(briefs)
    return EvalResult(system=system, recall_at_5=rec[5] / n, recall_at_10=rec[10] / n,
                      mrr=mrr_total / n, n_briefs=n)


def main() -> None:
    # TODO(claude-code): load briefs, wire up capgraph query + 3 baselines,
    # write a markdown results table to RESULTS_PATH. Implementation plan Task 7.
    raise NotImplementedError


if __name__ == "__main__":
    main()
