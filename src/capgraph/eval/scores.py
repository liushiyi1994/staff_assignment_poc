"""Checkpoint the deterministic score's *components*, so a weight sweep costs nothing.

The v2 work order assumes re-scoring is free. It is not, quite: the components a
weighted score combines (``specialization_match``, ``skill_overlap``, ``recency``,
``evidence_strength``) are produced by retrieval and expansion, which need the role
terms, which need the intent parse. This module makes the assumption true by paying for
each split's intent parses exactly once and checkpointing what retrieval produced:

    uv run python -m capgraph.eval.scores --split validation --stage stage7b_val   # spends
    uv run python -m capgraph.eval.scores --sweep --split validation               # offline

The dump is the *only* step that calls a model, and it calls only the intent parser —
no re-rank. Everything after it (any weight vector, any re-rank window width) is
arithmetic over the checkpoint.

What a sweep can and cannot measure is worth being exact about. Re-weighting reorders
the candidate pool, so it moves two things:

* the **score-only ranking**, which is a reported system, measurable here in full;
* the **re-rank window** — the top ``retrieval.rerank_top_k`` the LLM is shown. Whether
  the truth reaches that window is the ceiling on the full system's Hit@K, and it is
  also measurable here.

It cannot measure what the LLM would then do with a differently populated window. That
needs paid re-ranks, so the honest reading of a sweep is "this weight vector raises the
full system's ceiling", never "this weight vector raises the full system's score".
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

from .. import improvements
from ..query.engine import connected_driver, retrieve_role
from ..query.intent import parse_intent
from ..query.rank import (
    ALWAYS_SCORED_COMPONENTS,
    SCORE_COMPONENTS,
    combine_parts,
    score_candidate,
)
from ..query.retrieve import known_specializations
from ..settings import DATA_DIR, settings
from .fusion import dedupe
from .metrics import hit_at_k, load_manifest, mrr, query_context
from .systems import round_robin

SCORES_DIR = DATA_DIR / "eval" / "v2" / "scores"


def scores_path(split: str, *, subdir: str | None = None) -> Path:
    """Where one split's score components live.

    ``subdir`` names a checkpoint namespace under ``data/eval/`` — benchmark v3 dumps
    its own because its retrieval settings differ, and a checkpoint built under
    different retrieval is refused rather than re-used (see
    :func:`assert_config_matches`).
    """
    base = SCORES_DIR if subdir is None else DATA_DIR / "eval" / subdir
    return base / f"{split}.jsonl"


def retrieval_config() -> dict[str, object]:
    """Everything that can change a checkpointed score *component*.

    Deliberately not the whole run configuration: the components are weight-independent
    by construction, which is what makes the sweep free. Recording the weights here
    would refuse a checkpoint the sweep is designed to re-use. What it must refuse is a
    checkpoint built under different retrieval — a different intent model, wider arms,
    a different half-life — because those change the numbers themselves.

    A wave-1 improvement flag is recorded on the same terms and for the same reason:
    only when one is on, so the sidecars written before the flags existed still match.
    """
    return improvements.record({
        "manifest_version": str(settings["eval.manifest_version"]),
        "holdout_cutoff": str(settings["dataset.holdout_cutoff"]),
        "intent_model": str(settings["llm.intent_model"]),
        "embedding_model": str(settings["embedding.model"]),
        "retrieval": dict(settings["retrieval"]),
        "recency_half_life_days": int(settings["projections.recency_half_life_days"]),
    })


def config_path(split: str, *, path: Path | None = None) -> Path:
    path = scores_path(split) if path is None else path
    return path.with_suffix(".config.json")


def assert_config_matches(split: str, *, path: Path | None = None) -> None:
    """Refuse a component checkpoint built under different retrieval settings."""
    sidecar = config_path(split, path=path)
    if not sidecar.exists():
        return
    recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    current = retrieval_config()
    if recorded != current:
        differing = sorted(
            key for key in set(recorded) | set(current)
            if recorded.get(key) != current.get(key)
        )
        raise SystemExit(
            f"{sidecar} records retrieval settings that no longer match: "
            f"{', '.join(differing)}. The checkpointed score components were produced "
            "under the recorded settings; restore them, or move the checkpoint aside "
            "and re-dump the split."
        )


# ---------- checkpoint ----------

@dataclass(frozen=True)
class RoleScores:
    """One parsed role and the score components of every candidate it retrieved."""

    role: str
    parts: dict[str, dict[str, float]]      # person_id -> component -> value
    # Which retrieval arm(s) found each candidate. Empty for a benchmark-v2 checkpoint,
    # which predates the lexical arm and had nothing to distinguish.
    sources: dict[str, list[str]] = field(default_factory=dict)

    def ordering(self, weights: dict[str, float]) -> list[str]:
        """This role's candidates by weighted score, ties on person id."""
        scored = {
            person_id: combine_parts(parts, weights)
            for person_id, parts in self.parts.items()
        }
        return sorted(scored, key=lambda person_id: (-scored[person_id], person_id))

    def without_arm(self, arm: str) -> RoleScores:
        """This role as it would have been retrieved with one arm switched off.

        Only candidates that arm found *alone* disappear: the union means a person the
        vector or structured arm also reached is unaffected. Their score components are
        unchanged, because none of the four depends on which arm did the finding.
        """
        kept = {
            person_id: parts
            for person_id, parts in self.parts.items()
            if self.sources.get(person_id, []) != [arm]
        }
        return RoleScores(
            role=self.role,
            parts=kept,
            sources={k: v for k, v in self.sources.items() if k in kept},
        )


@dataclass(frozen=True)
class CaseScores:
    """Every role of one benchmark case, plus the identity the metrics need."""

    issue_id: str
    issue_key: str
    project_key: str
    truth: frozenset[str]
    roles: tuple[RoleScores, ...]

    def ordering(self, weights: dict[str, float]) -> list[str]:
        """The merged score-only ranking: each role's order, round-robin merged."""
        return round_robin([role.ordering(weights) for role in self.roles])

    def window(self, weights: dict[str, float], top_k: int) -> list[str]:
        """Everyone the re-rank would be shown: each role's top-K, deduplicated."""
        return dedupe(
            person_id for role in self.roles for person_id in role.ordering(weights)[:top_k]
        )

    def without_arm(self, arm: str) -> CaseScores:
        """This case as it would have been retrieved with one arm switched off."""
        return CaseScores(
            issue_id=self.issue_id,
            issue_key=self.issue_key,
            project_key=self.project_key,
            truth=self.truth,
            roles=tuple(role.without_arm(arm) for role in self.roles),
        )

    def pool(self) -> set[str]:
        """Every person any role retrieved: the pool candidate recall is measured on."""
        return {person_id for role in self.roles for person_id in role.parts}

    def to_json(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "issue_key": self.issue_key,
            "project_key": self.project_key,
            "truth_person_ids": sorted(self.truth),
            "roles": [
                {"role": role.role, "parts": role.parts, "sources": role.sources}
                for role in self.roles
            ],
        }

    @classmethod
    def from_json(cls, record: dict) -> CaseScores:
        return cls(
            issue_id=str(record["issue_id"]),
            issue_key=str(record["issue_key"]),
            project_key=str(record["project_key"]),
            truth=frozenset(str(person_id) for person_id in record["truth_person_ids"]),
            roles=tuple(
                RoleScores(
                    role=str(role["role"]),
                    parts={
                        str(person_id): {k: float(v) for k, v in parts.items()}
                        for person_id, parts in role["parts"].items()
                    },
                    sources={
                        str(person_id): [str(arm) for arm in arms]
                        for person_id, arms in (role.get("sources") or {}).items()
                    },
                )
                for role in record["roles"]
            ),
        )


def load_scores(split: str, *, path: Path | None = None) -> list[CaseScores]:
    path = scores_path(split) if path is None else path
    if not path.exists():
        raise SystemExit(f"no score checkpoint at {path}; run --split {split} first")
    assert_config_matches(split, path=path)
    cases = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                case = CaseScores.from_json(json.loads(line))
                cases[case.issue_id] = case              # a later record supersedes
    return [cases[issue_id] for issue_id in sorted(cases)]


def dump_split(
    split: str,
    *,
    stage: str,
    limit: int | None = None,
    path: Path | None = None,
    budget=None,
) -> int:
    """Parse intent and retrieve for every case in the split, then checkpoint the parts.

    Spends on the intent parse only. Resumes: cases already in the checkpoint are
    skipped, so an interrupted dump does not pay twice. ``budget`` is the track-wide
    pre-flight check to run first; it defaults to benchmark v2's.
    """
    path = scores_path(split) if path is None else path
    cases = sorted(load_manifest(splits=(split,)), key=lambda case: case.issue_id)[:limit]
    done = {case.issue_id for case in load_scores(split, path=path)} if path.exists() else set()
    pending = [case for case in cases if case.issue_id not in done]
    print(f"{split}: {len(cases)} cases, {len(done)} checkpointed, {len(pending)} to run")
    if not pending:
        return 0
    # Same track-wide ceiling the paid splits are checked against; this dump spends on
    # the intent parse only, so the per-case projection is deliberately far too
    # generous.
    if budget is None:
        from .run_v2 import enforce_v2_budget as budget

    print(f"pre-flight: projected at most ${budget(len(pending)):.2f}")

    from ..embeddings import embed
    from ..lexical import default_person_index

    embed(["warm up the local embedding model"])
    # Built before the loop for the same reason: a one-off corpus load, not per-case
    # work. Inert when retrieval.bm25_top_k is 0.
    lexical_index = (
        default_person_index() if int(settings["retrieval.bm25_top_k"]) > 0 else None
    )
    driver = connected_driver()
    path.parent.mkdir(parents=True, exist_ok=True)
    config_path(split, path=path).write_text(
        json.dumps(retrieval_config(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        specializations = known_specializations(driver)
        for index, case in enumerate(pending, 1):
            context = query_context(case)
            intent = parse_intent(context.query_text, specializations, stage=stage)
            roles = []
            for role in intent.roles:
                # Exactly the engine's sequence, including the graph-backed term
                # resolution: scoring with a different resolution would checkpoint
                # components the engine never computes.
                resolution, candidates = retrieve_role(
                    role,
                    context.query_text,
                    driver,
                    roster=context.eligible_roster,
                    as_of=context.as_of_time,
                    lexical_index=lexical_index,
                )
                for candidate in candidates:
                    score_candidate(candidate, role, resolution)
                roles.append(
                    RoleScores(
                        role=role.role,
                        parts={c.person_id: dict(c.score_parts) for c in candidates},
                        sources={
                            c.person_id: list(c.retrieval_sources) for c in candidates
                        },
                    )
                )
            record = CaseScores(
                issue_id=case.issue_id,
                issue_key=case.issue_key,
                project_key=case.project_key,
                truth=frozenset(str(p) for p in case.truth_person_ids),
                roles=tuple(roles),
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_json(), sort_keys=True) + "\n")
            print(
                f"  [{index}/{len(pending)}] {case.issue_key} "
                f"{len(roles)} role(s), {sum(len(r.parts) for r in roles)} candidates",
                flush=True,
            )
    finally:
        driver.close()
    return len(pending)


# ---------- sweep ----------

@dataclass(frozen=True)
class SweepRow:
    weights: dict[str, float]
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    mrr: float
    window_recall: float

    @property
    def label(self) -> str:
        return " ".join(f"{name.split('_')[0]}={value:.2f}" for name, value in
                        self.weights.items())


def evaluate_weights(
    cases: Sequence[CaseScores], weights: dict[str, float], *, top_k: int
) -> SweepRow:
    """Score-only metrics plus the share of cases whose truth reaches the re-rank window."""
    if not cases:
        raise ValueError("no cases to evaluate")
    hits = {k: 0.0 for k in (1, 5, 10)}
    reciprocal = 0.0
    reached = 0
    for case in cases:
        ranked = case.ordering(weights)
        for k in hits:
            hits[k] += hit_at_k(ranked, set(case.truth), k)
        reciprocal += mrr(ranked, set(case.truth))
        reached += bool(case.truth & set(case.window(weights, top_k)))
    n = len(cases)
    return SweepRow(
        weights=dict(weights),
        hit_at_1=hits[1] / n,
        hit_at_5=hits[5] / n,
        hit_at_10=hits[10] / n,
        mrr=reciprocal / n,
        window_recall=reached / n,
    )


def normalized(values: Iterable[float]) -> dict[str, float]:
    """A weight vector over SCORE_COMPONENTS, summing to one."""
    values = list(values)
    if len(values) != len(SCORE_COMPONENTS):
        raise ValueError(f"a weight vector needs {len(SCORE_COMPONENTS)} values")
    total = sum(values)
    if total <= 0:
        raise ValueError("weights must not sum to zero")
    return {name: round(value / total, 4) for name, value in zip(SCORE_COMPONENTS, values,
                                                                 strict=True)}


def coarse_grid(levels: Sequence[float] = (0.0, 1.0, 2.0, 3.0)) -> list[dict[str, float]]:
    """Every usable normalized combination of the levels, deduplicated.

    A coarse grid on purpose. With 30 validation cases, a fine grid does not find a
    better weighting — it finds the vector that happens to fit thirty coin flips.

    Vectors that give nothing to either always-scored component are excluded: a role
    that asks for no specializations and no skills has only those two components, so
    such a weighting cannot rank it at all. Excluding them here keeps the grid to
    weightings the engine could actually be configured with, rather than discovering the
    problem as a mid-sweep exception on whichever case happens to hit it.
    """
    seen: dict[tuple[float, ...], dict[str, float]] = {}
    for combination in product(levels, repeat=len(SCORE_COMPONENTS)):
        if sum(combination) <= 0:
            continue
        weights = normalized(combination)
        if not any(weights[name] > 0 for name in ALWAYS_SCORED_COMPONENTS):
            continue
        seen.setdefault(tuple(weights.values()), weights)
    return list(seen.values())


def marginal_effects(
    cases: Sequence[CaseScores],
    *,
    top_k: int | None = None,
    grid: Sequence[dict[str, float]] | None = None,
) -> dict[str, list[tuple[float, float, float]]]:
    """Each component's effect averaged over every grid point that holds it at 0 vs above.

    This, not the grid's argmax, is what a weight decision should be read from. A single
    top row on 30 cases is a coin flip; a component whose mean metric moves in one
    direction across a whole grid is a mechanism. Returns
    ``{component: [(weight, mean MRR, mean window recall), ...]}``.
    """
    top_k = int(settings["retrieval.rerank_top_k"]) if top_k is None else top_k
    grid = coarse_grid() if grid is None else grid
    rows = [(weights, evaluate_weights(cases, weights, top_k=top_k)) for weights in grid]
    effects: dict[str, list[tuple[float, float, float]]] = {}
    for component in SCORE_COMPONENTS:
        buckets: dict[float, list[SweepRow]] = {}
        for weights, row in rows:
            buckets.setdefault(round(weights[component], 2), []).append(row)
        effects[component] = [
            (
                weight,
                sum(row.mrr for row in bucket) / len(bucket),
                sum(row.window_recall for row in bucket) / len(bucket),
            )
            for weight, bucket in sorted(buckets.items())
            if len(bucket) >= 5                      # ignore thinly populated levels
        ]
    return effects


def sweep(
    cases: Sequence[CaseScores],
    *,
    top_k: int | None = None,
    grid: Sequence[dict[str, float]] | None = None,
) -> list[SweepRow]:
    """Every grid point, best window recall first, then MRR. Ties keep grid order."""
    top_k = int(settings["retrieval.rerank_top_k"]) if top_k is None else top_k
    rows = [
        evaluate_weights(cases, weights, top_k=top_k)
        for weights in (coarse_grid() if grid is None else grid)
    ]
    return sorted(rows, key=lambda row: (-row.window_recall, -row.mrr))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checkpoint and sweep score components")
    parser.add_argument("--split", default="validation", choices=("validation", "test"))
    parser.add_argument("--stage", default="stage7b_val", help="cost-log stage for the dump")
    parser.add_argument("--limit", type=int, help="dump only the first N cases")
    parser.add_argument("--sweep", action="store_true", help="offline: sweep the checkpoint")
    parser.add_argument("--top-n", type=int, default=12, help="sweep rows to print")
    parser.add_argument(
        "--subdir",
        help="checkpoint namespace under data/eval/ (default: benchmark v2's)",
    )
    args = parser.parse_args(argv)

    path = scores_path(args.split, subdir=args.subdir)
    if not args.sweep:
        dump_split(args.split, stage=args.stage, limit=args.limit, path=path)
        return 0

    cases = load_scores(args.split, path=path)
    top_k = int(settings["retrieval.rerank_top_k"])
    rows = sweep(cases, top_k=top_k)
    current = evaluate_weights(cases, dict(settings["scoring.weights"]), top_k=top_k)
    print(f"{len(cases)} {args.split} cases, re-rank window {top_k}\n")
    header = f"| {'Weights':52s} | Hit@1 | Hit@5 | Hit@10 |   MRR | Window recall |"
    print(header)
    print("|" + "-" * (len(header) - 2) + "|")
    for row in [current, *rows[: args.top_n]]:
        print(
            f"| {row.label:52s} | {row.hit_at_1:5.3f} | {row.hit_at_5:5.3f} | "
            f"{row.hit_at_10:6.3f} | {row.mrr:5.3f} | {row.window_recall:13.3f} |"
        )
    print("\nfirst row is the configured weighting; the rest are the sweep's best")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
