"""The temporal benchmark harness: run every system over the frozen manifest.

    uv run python -m capgraph.eval.run_eval --split validation      # spends
    uv run python -m capgraph.eval.run_eval --split test            # spends
    uv run python -m capgraph.eval.run_eval --report-only           # offline

Five systems are scored on the same 150 cases: the graph system with and without its
LLM re-rank (one run, two rankings — see eval/systems.py) and three roster-restricted
baselines (eval/baselines.py). The metric code is in eval/metrics.py and is re-exported
here, so ``from capgraph.eval.run_eval import evaluate`` keeps working.

Three rules the harness enforces rather than assumes:

* **Nothing is dropped silently.** Every manifest case produces a checkpoint record
  per system — a ranking or a recorded failure with its reason — and the report
  reconciles the counts.
* **A re-run costs nothing already paid for.** Records are appended per case and
  system to ``data/eval/runs/<split>.jsonl``; an interrupted run resumes from there.
  The checkpoint carries the run's configuration digest and the harness refuses to
  mix results produced under different settings.
* **Splits stay apart.** ``--split`` runs one split and the report keeps them in
  separate tables. Anything learned on validation must be frozen before the test
  split is run, which is the only reason the split exists.

Ranking outputs are replayed through :func:`evaluate`, so the roster and candidate-pool
invariants are checked against the manifest at scoring time as well as at run time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .. import improvements
from ..evidence import EvidenceView
from ..llm import CostControlError, stage_budget_usd, stage_cost_so_far
from ..settings import DATA_DIR, settings
from .baselines import BASELINE_SYSTEMS, build_baselines
from .contracts import BenchmarkQueryContext, RankingOutput
from .holdout import MANIFEST_VERSION, BenchmarkManifestEntry
from .metrics import (
    HEADER_ROW,
    HEADER_RULE,
    EvaluationResult,
    MetricSummary,
    adapt_text_ranker,
    candidate_recall,
    evaluate,
    hit_at_k,
    load_manifest,
    manifest_summary,
    mrr,
    print_manifest_summary,
    query_context,
    recall_at_k,
    summary_row,
    write_results,
)
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE, GRAPH_SYSTEMS, CapGraphSystem

__all__ = [
    "BenchmarkQueryContext",
    "RankingOutput",
    "adapt_text_ranker",
    "candidate_recall",
    "evaluate",
    "hit_at_k",
    "load_manifest",
    "mrr",
    "recall_at_k",
    "write_results",
]

EVAL_DIR = DATA_DIR / "eval"
RUNS_DIR = EVAL_DIR / "runs"
RESULTS_MD = EVAL_DIR / "results.md"
RESULTS_JSON = EVAL_DIR / "results.json"
TRACKED_REPORT = Path(__file__).resolve().parents[3] / "docs" / "eval-results.md"

ALL_SYSTEMS = (*GRAPH_SYSTEMS, *BASELINE_SYSTEMS)
SPLITS = ("validation", "test")

# The tracked report is written in sections, oldest first, each below its own marker:
# v1 above V2_MARKER, benchmark v2 between the two, benchmark v3 below V3_MARKER.
# Regenerating any one of them must not disturb the others — every set of numbers is
# frozen once its split has been run, and a re-report must not silently re-date or
# re-digest a section it does not own (see eval/run_v2.py and eval/run_v3.py).
V2_MARKER = "<!-- benchmark-v2 -->"
V3_MARKER = "<!-- benchmark-v3 -->"
# Benchmark v4 is a different instrument on a different manifest, so it is a section of
# its own below this marker rather than more rows in v1-v3's tables (eval/run_v4.py).
V4_MARKER = "<!-- benchmark-v4 -->"

SYSTEM_LABELS = {
    CAPGRAPH_FULL: "capgraph (score + LLM re-rank)",
    CAPGRAPH_SCORE: "capgraph (deterministic score only)",
    "bm25": "BM25 over pre-cutoff ticket text",
    "vector_only": "pure vector (plain RAG)",
    "most_active": "most-active in project",
}


# ---------- run configuration ----------

def run_config() -> dict[str, object]:
    """Everything that can change a number, recorded with the results.

    The wave-1 improvement block is appended only when one of its flags is on (see
    :mod:`capgraph.improvements`), so a default run digests exactly as it did before
    those flags existed and the frozen v1/v2/v3 checkpoints stay readable.
    """
    return improvements.record({
        "manifest_version": MANIFEST_VERSION,
        "seed": int(settings["eval.seed"]),
        "holdout_cutoff": str(settings["dataset.holdout_cutoff"]),
        "projects": list(settings["dataset.projects"]),
        "intent_model": str(settings["llm.intent_model"]),
        "rerank_model": str(settings["llm.rerank_model"]),
        "embedding_model": str(settings["embedding.model"]),
        "retrieval": dict(settings["retrieval"]),
        "scoring": dict(settings["scoring"]),
        "recency_half_life_days": int(settings["projections.recency_half_life_days"]),
        "baselines": dict(settings["eval.baselines"]),
        "stage": str(settings["eval.stage_name"]),
    })


def config_digest(config: dict[str, object] | None = None) -> str:
    payload = json.dumps(run_config() if config is None else config, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------- checkpoints ----------

def checkpoint_path(split: str, *, runs_dir: Path | None = None) -> Path:
    """Where one split's records live. ``runs_dir`` gives an experiment its own
    namespace: a v2 run must never append to, or be scored together with, the frozen
    v1 checkpoint."""
    return (RUNS_DIR if runs_dir is None else runs_dir) / f"{split}.jsonl"


def load_checkpoint(
    split: str, *, path: Path | None = None, runs_dir: Path | None = None
) -> dict[tuple[str, str], dict]:
    """Completed records keyed by (system, issue_id); a later record supersedes."""
    path = checkpoint_path(split, runs_dir=runs_dir) if path is None else path
    records: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            records[(record["system"], record["issue_id"])] = record
    return records


def append_record(
    split: str, record: dict, *, path: Path | None = None, runs_dir: Path | None = None
) -> None:
    path = checkpoint_path(split, runs_dir=runs_dir) if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def assert_checkpoint_matches_config(records: Iterable[dict], digest: str) -> None:
    """Refuse to extend a checkpoint written under a different configuration."""
    stale = sorted({record.get("config_digest", "<none>") for record in records} - {digest})
    if stale:
        raise SystemExit(
            f"checkpoint holds results from configuration(s) {', '.join(stale)} but the "
            f"current configuration is {digest}. Metrics must not mix configurations: "
            "restore the settings, or move data/eval/runs/ aside and re-run the split."
        )


def _record(
    *,
    split: str,
    system: str,
    case: BenchmarkManifestEntry,
    digest: str,
    output: RankingOutput | None = None,
    error: str | None = None,
    detail: dict | None = None,
) -> dict:
    record: dict[str, object] = {
        "split": split,
        "system": system,
        "issue_id": case.issue_id,
        "issue_key": case.issue_key,
        "project_key": case.project_key,
        "config_digest": digest,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if error is not None:
        record["error"] = error
        return record
    assert output is not None
    record.update(
        {
            "ranked_ids": list(output.ranked_ids),
            "candidate_ids": None if output.candidate_ids is None else list(output.candidate_ids),
            "latency_ms": round(float(output.latency_ms or 0.0), 2),
            "cost_usd": round(float(output.cost_usd), 6),
        }
    )
    if detail:
        record["detail"] = detail
    return record


def assert_within_roster(output: RankingOutput, case: BenchmarkManifestEntry) -> None:
    """A system that leaves its frozen roster has failed the case, not won it.

    :func:`evaluate` checks this again when the records are scored. Checking here too
    means the offending output is recorded as a failure with a reason instead of
    reaching the report and taking the whole run down with it.
    """
    roster = {str(person_id) for person_id in case.eligible_roster}
    for label, ids in (
        ("ranking", output.ranked_ids),
        ("candidate pool", output.candidate_ids or ()),
    ):
        outside = sorted({str(person_id) for person_id in ids} - roster)
        if outside:
            raise ValueError(
                f"{label} for {case.issue_id} leaves the frozen roster: {outside}"
            )


def replay_ranker(records: dict[str, dict]):
    """Score checkpointed rankings through :func:`evaluate`'s invariant checks."""

    def rank_fn(context: BenchmarkQueryContext) -> RankingOutput:
        record = records[context.issue_id]
        return RankingOutput(
            ranked_ids=record["ranked_ids"],
            candidate_ids=record.get("candidate_ids"),
            latency_ms=record.get("latency_ms"),
            cost_usd=float(record.get("cost_usd") or 0.0),
        )

    return rank_fn


# ---------- running ----------

def run_split(
    split: str,
    *,
    systems: Sequence[str] = ALL_SYSTEMS,
    limit: int | None = None,
    stage: str | None = None,
    runs_dir: Path | None = None,
    config: dict[str, object] | None = None,
    cases: Sequence[BenchmarkManifestEntry] | None = None,
    manifest_version: str = MANIFEST_VERSION,
) -> dict[str, int]:
    """Run the requested systems over one split, resuming from its checkpoint.

    ``cases`` and ``manifest_version`` let a later benchmark supply its own manifest —
    benchmark v4's work packages, in their rewritten or un-rewritten brief variant —
    without a second copy of the run loop, the checkpointing, or the roster guard.
    """
    unknown = sorted(set(systems) - set(ALL_SYSTEMS))
    if unknown:
        raise ValueError(f"unknown system(s): {', '.join(unknown)}")
    source = load_manifest(splits=(split,)) if cases is None else cases
    cases = sorted(source, key=lambda case: case.issue_id)
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise SystemExit(f"no {split} cases in the manifest")

    digest = config_digest(config)
    done = load_checkpoint(split, runs_dir=runs_dir)
    assert_checkpoint_matches_config(done.values(), digest)
    stage = stage or str(settings["eval.stage_name"])

    baseline_names = [name for name in systems if name in BASELINE_SYSTEMS]
    graph_names = [name for name in systems if name in GRAPH_SYSTEMS]
    pending = [
        (case, name)
        for case in cases
        for name in systems
        if (name, case.issue_id) not in done
    ]
    counts = {"cases": len(cases), "skipped": len(cases) * len(systems) - len(pending),
              "ran": 0, "failed": 0}
    print(
        f"{split}: {len(cases)} cases x {len(systems)} systems, "
        f"{counts['skipped']} already checkpointed, config {digest}"
    )
    if not pending:
        return counts

    rankers: dict[str, object] = {}
    if baseline_names:
        started = time.perf_counter()
        view = EvidenceView.load()
        view.write_document_cache()
        print(
            f"evidence view: {len(view.tickets)} pre-cutoff tickets, "
            f"{len(view.documents)} people ({time.perf_counter() - started:.1f}s)"
        )
        rankers.update(build_baselines(view, names=baseline_names))

    driver = None
    if graph_names:
        from ..embeddings import embed
        from ..lexical import default_person_index
        from ..query.engine import connected_driver

        # Load the sentence transformer before the first case is timed. It is a
        # once-per-process startup cost, not a per-query one, and leaving it inside
        # case 1 would put several seconds of model loading into the latency table.
        # The lexical arm's BM25 corpus is warmed here for the same reason, and only
        # when the arm is switched on.
        embed(["warm up the local embedding model"])
        lexical_index = (
            default_person_index() if int(settings["retrieval.bm25_top_k"]) > 0 else None
        )
        driver = connected_driver()
        graph = CapGraphSystem(driver, stage=stage, lexical_index=lexical_index)
        budget = stage_budget_usd(stage)
        print(
            f"graph system: stage '{stage}', logged spend so far "
            f"${stage_cost_so_far(stage):.4f} of ${budget:.2f}"
        )

    try:
        for index, case in enumerate(cases, 1):
            context = query_context(case, expected_version=manifest_version)
            note = ""

            for name in baseline_names:
                if (name, case.issue_id) in done:
                    continue
                counts["ran"] += 1
                try:
                    output = rankers[name].rank(context)
                    assert_within_roster(output, case)
                except Exception as error:                       # a failure is a result
                    counts["failed"] += 1
                    append_record(split, _record(split=split, system=name, case=case,
                                                 digest=digest, error=repr(error)),
                                  runs_dir=runs_dir)
                else:
                    append_record(split, _record(split=split, system=name, case=case,
                                                 digest=digest, output=output),
                                  runs_dir=runs_dir)

            todo = [name for name in graph_names if (name, case.issue_id) not in done]
            if todo:
                counts["ran"] += len(todo)
                try:
                    outputs, detail = graph.run(context)
                    for name in todo:
                        assert_within_roster(outputs[name], case)
                except CostControlError as error:
                    # Refused before the request was sent. Stopping is the only correct
                    # response; the checkpoint means nothing already paid for is lost.
                    print(f"\nstopped at case {index}/{len(cases)} ({case.issue_id}): "
                          f"{error}", file=sys.stderr)
                    raise SystemExit(2) from error
                except Exception as error:
                    counts["failed"] += len(todo)
                    note = f" FAILED {error!r}"
                    for name in todo:
                        append_record(split, _record(split=split, system=name, case=case,
                                                     digest=digest, error=repr(error)),
                                      runs_dir=runs_dir)
                else:
                    for name in todo:
                        append_record(
                            split,
                            _record(split=split, system=name, case=case, digest=digest,
                                    output=outputs[name],
                                    detail=detail if name == CAPGRAPH_FULL else None),
                            runs_dir=runs_dir,
                        )
                    note = (
                        f" {detail['timings_ms'].get('total_ms', 0.0) / 1000:.1f}s"
                        f" spend ${stage_cost_so_far(stage):.4f}"
                    )
            print(
                f"  [{index}/{len(cases)}] {case.issue_key} {case.project_key}{note}",
                flush=True,
            )
    finally:
        if driver is not None:
            driver.close()
    return counts


# ---------- reporting ----------

def score_split(
    split: str,
    systems: Sequence[str] = ALL_SYSTEMS,
    *,
    runs_dir: Path | None = None,
    manifest_cases: Sequence[BenchmarkManifestEntry] | None = None,
    manifest_version: str = MANIFEST_VERSION,
) -> tuple[list[EvaluationResult], dict[str, list[dict]]]:
    """Score every checkpointed system for one split; return results and failures."""
    source = load_manifest(splits=(split,)) if manifest_cases is None else manifest_cases
    cases = {case.issue_id: case for case in source}
    done = load_checkpoint(split, runs_dir=runs_dir)
    results: list[EvaluationResult] = []
    failures: dict[str, list[dict]] = {}
    for name in systems:
        records = {
            issue_id: record
            for (system, issue_id), record in done.items()
            if system == name
        }
        scored = {i: r for i, r in records.items() if "error" not in r}
        broken = [
            {"issue_id": i, "issue_key": cases[i].issue_key if i in cases else "",
             "error": r["error"]}
            for i, r in sorted(records.items()) if "error" in r
        ]
        if broken:
            failures[name] = broken
        briefs = [cases[i] for i in sorted(scored) if i in cases]
        if not briefs:
            continue
        results.append(
            evaluate(
                name, replay_ranker(scored), briefs, expected_version=manifest_version
            )
        )
    return results, failures


def roster_sizes() -> dict[str, int]:
    sizes: dict[str, int] = {}
    for case in load_manifest(splits=None):
        if case.split != "excluded":
            sizes[case.project_key] = len(case.eligible_roster)
    return dict(sorted(sizes.items()))


def _label(system: str) -> str:
    return f"{system} — {SYSTEM_LABELS.get(system, system)}"


COMPARED_METRICS = ("hit_at_1", "hit_at_5", "hit_at_10", "mrr")


def _versus_baselines(results: Sequence[EvaluationResult]) -> list[str]:
    """Full system against the *best* baseline on each metric, generated not asserted.

    Per metric, whichever baseline scored highest is the one shown. Comparing against
    an average, or against a fixed baseline chosen in advance, would let a weak result
    hide behind the baselines it happens to beat.
    """
    full = next((r for r in results if r.system == CAPGRAPH_FULL), None)
    baselines = [r for r in results if r.system in BASELINE_SYSTEMS]
    if full is None or not baselines:
        return []
    lines = [
        "",
        "The graph system against the strongest baseline on each metric — the best "
        "baseline per column, not an average, and reported whichever way it falls:",
        "",
        "| Metric | capgraph_full | Best baseline | Δ |",
        "|---|---:|---|---:|",
    ]
    for metric in COMPARED_METRICS:
        best = max(baselines, key=lambda r: (getattr(r, metric), r.system))
        value, rival = getattr(full, metric), getattr(best, metric)
        lines.append(
            f"| {metric.replace('_at_', '@').replace('hit@', 'Hit@').replace('mrr', 'MRR')} "
            f"| {value:.3f} | {rival:.3f} (`{best.system}`) | {value - rival:+.3f} |"
        )
    return lines


def run_diagnostics(split: str, *, runs_dir: Path | None = None) -> dict[str, object]:
    """What the graph system actually did on a split, read back from the checkpoint.

    These are the numbers that decide whether the headline table can be read at face
    value: how often a brief parsed into more than one role, how often the re-rank
    returned fewer people than it was given (and therefore how often the padding rule
    could have mattered), and what the evidence validator rejected.
    """
    records = [
        record
        for (system, _), record in load_checkpoint(split, runs_dir=runs_dir).items()
        if system == CAPGRAPH_FULL and "detail" in record
    ]
    if not records:
        return {}
    returned = sorted(record["detail"]["n_ranked_by_rerank"] for record in records)
    pools = sorted(len(record["candidate_ids"] or ()) for record in records)
    rejected = [
        problem for record in records for problem in record["detail"].get("rejected", ())
    ]
    return {
        "cases": len(records),
        "multi_role_cases": sum(len(r["detail"]["roles"]) > 1 for r in records),
        "llm_calls": sum(r["detail"]["n_llm_calls"] for r in records),
        "rerank_entries_min": returned[0],
        "rerank_entries_median": returned[len(returned) // 2],
        "cases_below_ten_ranked": sum(value < 10 for value in returned),
        "candidate_pool_min": pools[0],
        "candidate_pool_median": pools[len(pools) // 2],
        "candidate_pool_max": pools[-1],
        "rejected_rerank_entries": len(rejected),
        # "<person>: <reason>: <offending keys>" -> the reason class alone.
        "rejection_reasons": sorted(
            {problem.split(": ", 1)[-1].split(":")[0] for problem in rejected}
        ),
    }


def _results_payload(
    per_split: dict[str, tuple[list[EvaluationResult], dict[str, list[dict]]]]
) -> dict:
    def summary(value: MetricSummary) -> dict:
        return {
            key: getattr(value, key)
            for key in (
                "n_briefs", "hit_at_1", "hit_at_5", "hit_at_10", "recall_at_5",
                "recall_at_10", "mrr", "candidate_recall", "latency_ms_mean",
                "latency_ms_median", "latency_ms_p95", "cost_usd_total",
            )
        }

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": run_config(),
        "config_digest": config_digest(),
        "manifest": manifest_summary(),
        "roster_sizes": roster_sizes(),
        "splits": {
            split: {
                "systems": {
                    result.system: {
                        "overall": summary(result),
                        "per_project": {
                            project: summary(value)
                            for project, value in result.per_project.items()
                        },
                    }
                    for result in results
                },
                "failures": failures,
            }
            for split, (results, failures) in per_split.items()
        },
    }


def render_report(
    per_split: dict[str, tuple[list[EvaluationResult], dict[str, list[dict]]]],
    *,
    title: str = "Temporal benchmark results",
) -> str:
    """The reviewable report: headline tables per split, per project, and the caveats."""
    config = run_config()
    sizes = roster_sizes()
    summary = manifest_summary()
    lines = [
        f"# {title}",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} from manifest "
        f"`{config['manifest_version']}` (seed {config['seed']}), configuration digest "
        f"`{config_digest()}`.",
        "",
        "Historical assignment is the *prediction target*, not proof that the assignee "
        "was the uniquely or optimally qualified person. The defensible claim is "
        "narrowly predictive: given only information available when a historical issue "
        "was created, the system ranks its eventual assignee in the top K this often.",
        "",
        "## Run configuration",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Cases | {summary['selected']} selected "
        f"({', '.join(f'{k} {v}' for k, v in summary['splits'].items())}) |",
        f"| Holdout cutoff | {config['holdout_cutoff']} (graph evidence strictly before) |",
        f"| Projects / roster size | "
        f"{', '.join(f'{k} {v}' for k, v in sizes.items())} |",
        f"| Intent model | `{config['intent_model']}` |",
        f"| Re-rank model | `{config['rerank_model']}` |",
        f"| Embedding model | `{config['embedding_model']}` (local, "
        f"{settings['embedding.dims']} dims) |",
        f"| Retrieval | vector top-{config['retrieval']['vector_top_k']} ∪ structured "
        f"top-{config['retrieval']['structured_top_k']}, re-rank top-"
        f"{config['retrieval']['rerank_top_k']} |",
        f"| Score weights | "
        f"{', '.join(f'{k} {v}' for k, v in config['scoring']['weights'].items())} |",
        f"| Recency | half-life {config['recency_half_life_days']} d, recomputed at each "
        "case's as-of time |",
        f"| Cost-log stage | `{config['stage']}` |",
        "",
        "## Systems",
        "",
        "| System | What it is |",
        "|---|---|",
        f"| `{CAPGRAPH_FULL}` | Full pipeline: intent parse, vector ∪ structured retrieval, "
        "weighted score, LLM re-rank of the top-K with cited evidence. |",
        f"| `{CAPGRAPH_SCORE}` | Ablation: the same retrieval and weighted score, no "
        "re-rank. Still uses the intent parse, so it isolates the re-rank, not every "
        "LLM call. |",
        "| `bm25` | BM25 over one concatenated pre-cutoff evidence document per person. |",
        "| `vector_only` | Plain RAG: the same evidence text embedded per ticket, person "
        "scored by nearest ticket. |",
        "| `most_active` | Pre-cutoff evidence-ticket count in the case's project; "
        "ignores the brief. |",
        "",
        "Every system ranks only the case's frozen same-project eligible roster. The three "
        "baselines rank the whole roster, so their candidate recall is 1.0 by construction; "
        "the graph system ranks its retrieved union, so its candidate recall is a real "
        "measurement and bounds its Hit@K.",
        "",
        "## Method and leakage guards",
        "",
        "A benchmark case is a real issue treated as a brief at its **creation** time. "
        "Nothing later is allowed to influence any system:",
        "",
        "1. **Query text** is the issue's creation-time summary and description, "
        "reconstructed from the change log, with identifiers, pseudonyms, mentions, and "
        "email addresses stripped. Comments and later edits are never substituted in.",
        "2. **Truth** is the assignee reconstructed at the safe resolution boundary. The "
        "dump's final-assignee snapshot is audit-only, and a case whose truth is outside "
        "the frozen roster was excluded at manifest build time.",
        "3. **Roster** is the same-project eligible set frozen at the holdout cutoff, and "
        "it travels into Cypher as a parameter — the structured arm matches inside it, the "
        "vector arm filters the index result to it, and the harness refuses (and records "
        "as a failure) any output naming someone outside it.",
        "4. **Evidence** is the pre-cutoff Stage 1 view for every system: the graph was "
        "built from those buckets, and BM25 and the vector baseline read the same "
        "sanitized ticket text. \"Pre-cutoff resolved tickets\" therefore means retained "
        "evidence tickets — buckets too small to extract from were dropped upstream, and "
        "all four systems inherit that truncation equally.",
        "5. **Recency** is recomputed for every capability edge from its stored `last_used` "
        "at the case's as-of time, through the same Stage 4 `decay()` the pipeline uses. "
        "The graph's stored decay is frozen at the cutoff, which is earlier than every "
        "query time here, and is never read during evaluation. Wall-clock time is not an "
        "input anywhere.",
        "6. **Splits.** The 30 validation cases were run first and reviewed; the "
        "configuration was then frozen and the 120 test cases were run once, under the "
        "configuration digest recorded above. Both splits are reported separately.",
        "",
        "Latency excludes one-time process startup (the local embedding model is loaded "
        "before the first case is timed). Cost is the spend the LLM gateway actually "
        "logged, retries included.",
    ]

    for split in SPLITS:
        if split not in per_split:
            continue
        results, failures = per_split[split]
        expected = len(load_manifest(splits=(split,)))
        lines += [
            "",
            f"## {split.capitalize()} split",
            "",
            "Case accounting — every manifest case is scored or listed as a failure:",
            "",
            "| System | Cases in split | Scored | Failed |",
            "|---|---:|---:|---:|",
        ]
        for result in results:
            n_failed = len(failures.get(result.system, ()))
            lines.append(
                f"| `{result.system}` | {expected} | {result.n_briefs} | {n_failed} |"
            )
        lines += ["", HEADER_ROW, HEADER_RULE]
        for result in results:
            lines.append(summary_row(_label(result.system), result))
        lines += _versus_baselines(results)
        if failures:
            lines += ["", "### Failures", "", "| System | Issue | Reason |", "|---|---|---|"]
            for system, broken in sorted(failures.items()):
                for failure in broken:
                    lines.append(
                        f"| {system} | {failure['issue_key'] or failure['issue_id']} | "
                        f"{failure['error']} |"
                    )
        lines += ["", f"### {split.capitalize()} split by project", "", HEADER_ROW, HEADER_RULE]
        for result in results:
            for project_key, value in result.per_project.items():
                lines.append(
                    summary_row(f"{result.system} / {project_key} "
                                f"(roster {sizes.get(project_key, 0)})", value)
                )
        diagnostics = run_diagnostics(split)
        if diagnostics:
            lines += [
                "",
                f"### {split.capitalize()} run diagnostics (graph system)",
                "",
                "| Measure | Value |",
                "|---|---|",
                *(
                    f"| {key.replace('_', ' ')} | "
                    f"{', '.join(value) if isinstance(value, list) else value} |"
                    for key, value in diagnostics.items()
                ),
            ]
    lines += [
        "",
        "## Caveats",
        "",
        "- **The target is assignee prediction.** Ranking the historical assignee first is "
        "evidence that the system finds relevant, recent, evidence-backed people — not "
        "that it found the *best* person. Several roster members may have been equally "
        "qualified; the dataset cannot say.",
        "- **Hit@K must be read against roster size.** A 21-person roster (EVG) makes "
        "Hit@10 far easier than a 105-person one (DM), which is why every table is also "
        "broken down per project with its roster size.",
        "- **Candidate recall is the graph system's ceiling.** Its Hit@K can never "
        "exceed the share of cases whose truth its retrieval union contained; the gap "
        "between the two is retrieval loss, not ranking loss, and the baselines have no "
        "such ceiling because they rank the entire roster.",
        "- **A margin over BM25 is the honest bar.** BM25 over the same evidence is free "
        "and fast; a per-metric delta above is the only claim this benchmark supports, "
        "and it is reported in whichever direction it falls.",
        "- **Public OSS Jira is not agency work.** Projects, vocabulary, and assignment "
        "practice all differ; the pipeline is domain-agnostic, the numbers are not.",
        "- **Identities are project-qualified pseudonyms.** No cross-project identity is "
        "inferred, and no result here is usable for a real employment decision.",
        "- **The re-rank is scored on the pool it was given.** Where it omitted or rejected "
        "a shortlisted person, that person is appended in deterministic score order, so "
        "the ablation compares ordering rather than coverage.",
    ]
    return "\n".join(lines) + "\n"


def write_report(
    per_split: dict[str, tuple[list[EvaluationResult], dict[str, list[dict]]]],
    *,
    markdown_path: Path = RESULTS_MD,
    json_path: Path = RESULTS_JSON,
    tracked_path: Path | None = TRACKED_REPORT,
) -> None:
    markdown = render_report(per_split)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(_results_payload(per_split), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if tracked_path is not None:
        # Keep every later section that already follows the first marker: this writer
        # owns the v1 half of the tracked report and nothing else.
        existing = tracked_path.read_text(encoding="utf-8") if tracked_path.exists() else ""
        _, marker, tail = existing.partition(V2_MARKER)
        tracked_path.write_text(markdown + marker + tail, encoding="utf-8")


def report(splits: Sequence[str] = SPLITS, systems: Sequence[str] = ALL_SYSTEMS) -> None:
    per_split = {}
    for split in splits:
        results, failures = score_split(split, systems)
        if results or failures:
            per_split[split] = (results, failures)
    if not per_split:
        raise SystemExit(
            f"no checkpointed results in {RUNS_DIR}; run a split before reporting"
        )
    write_report(per_split)
    print(render_report(per_split))
    print(f"\nwrote {RESULTS_MD}, {RESULTS_JSON}, {TRACKED_REPORT}")


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the temporal benchmark")
    parser.add_argument(
        "--split", choices=[*SPLITS, "all"],
        help="split to run (spends on the graph system); omit with --report-only",
    )
    parser.add_argument(
        "--systems", default=",".join(ALL_SYSTEMS),
        help=f"comma-separated subset of {','.join(ALL_SYSTEMS)}",
    )
    parser.add_argument("--limit", type=int, help="run only the first N cases (smoke test)")
    parser.add_argument("--stage", help="cost-log stage name (default: eval.stage_name)")
    parser.add_argument("--report-only", action="store_true",
                        help="rebuild the report from existing checkpoints")
    parser.add_argument("--manifest-summary", action="store_true",
                        help="reconcile manifest counts and exit")
    args = parser.parse_args(argv)

    if args.manifest_summary:
        print_manifest_summary()
        return 0

    systems = [name.strip() for name in args.systems.split(",") if name.strip()]
    if args.report_only or args.split is None:
        report(systems=systems)
        return 0

    splits = SPLITS if args.split == "all" else (args.split,)
    totals: dict[str, int] = defaultdict(int)
    for split in splits:
        for key, value in run_split(
            split, systems=systems, limit=args.limit, stage=args.stage
        ).items():
            totals[key] += value
    print(json.dumps(dict(sorted(totals.items())), indent=2))
    report(splits=splits, systems=systems)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
