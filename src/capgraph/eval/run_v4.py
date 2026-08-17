"""Benchmark v4: run every system over the work-package manifest, and report it.

    uv run python -m capgraph.eval.run_v4 --split validation --engine v3frozen   # spends
    uv run python -m capgraph.eval.run_v4 --split validation --briefs raw        # spends
    uv run python -m capgraph.eval.run_v4 --split test --engine v2frozen         # spends, once
    uv run python -m capgraph.eval.run_v4 --baselines --split all                # offline
    uv run python -m capgraph.eval.run_v4 --report                               # offline

What is new here is the *instrument*, not the tuning. Three consequences shape this
module:

* **v4 numbers are never rows next to v1-v3's.** They are written below their own
  marker in ``docs/eval-results.md`` and into their own checkpoint namespace, and the
  report says in its first paragraph what changed about the measurement.
* **Two frozen engine configurations, one manifest.** ``v3frozen`` is the current
  default; ``v2frozen`` restores the four settings v3 changed. They are applied as
  scoped overrides (``settings.overridden``) so a single command runs either arm and
  the configuration digest records which one ran.
* **Two brief variants, same cases.** ``rewritten`` is the benchmark; ``raw`` is the
  un-rewritten package text on the same as-of times, rosters, and truth. Running the
  validation split both ways is how the rewrite's own effect gets measured instead of
  assumed.

Exposure budget: everything is chosen on validation, which may be run as often as it
needs to be. The test split is run **once per engine configuration**, in the rewritten
variant, after the configuration is frozen.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..settings import DATA_DIR, PROMPTS_DIR, settings
from .costs import spend_by_purpose, spend_by_stage
from .metrics import HEADER_RULE, EvaluationResult, hit_at_k, mrr, recall_at_k, summary_row
from .packages import (
    BRIEF_VARIANTS,
    PACKAGE_MANIFEST_VERSION,
    RAW,
    REWRITTEN,
    PackageManifestEntry,
    load_package_manifest,
    manifest_summary,
)
from .baselines import BASELINE_SYSTEMS
from .paired import paired_binary, paired_bootstrap, render_paired
from .run_eval import (
    ALL_SYSTEMS,
    SPLITS,
    TRACKED_REPORT,
    V4_MARKER,
    config_digest,
    load_checkpoint,
    run_config,
    run_diagnostics,
    run_split,
    score_split,
)
from .systems import CAPGRAPH_FULL, GRAPH_SYSTEMS

V4_DIR = DATA_DIR / "eval" / "v4"
DEFAULT_ENGINE = "v3frozen"
V2_ENGINE = "v2frozen"

COMPARED = ("hit_at_1", "hit_at_5", "hit_at_10", "recall_at_5", "recall_at_10", "mrr",
            "candidate_recall")
METRIC_LABELS = {
    "hit_at_1": "Hit@1",
    "hit_at_5": "Hit@5",
    "hit_at_10": "Hit@10",
    "recall_at_5": "Recall@5",
    "recall_at_10": "Recall@10",
    "mrr": "MRR",
    "candidate_recall": "Candidate recall",
}


class V4BudgetError(RuntimeError):
    """The v4 track's own ceiling, checked before a split starts."""


# ---------- namespaces and configuration ----------

def engines() -> dict[str, dict[str, object]]:
    configured = settings["eval.v4.engine_configs"]
    if not isinstance(configured, Mapping):
        raise TypeError("eval.v4.engine_configs must be a mapping of name to overrides")
    return {str(name): dict(overrides or {}) for name, overrides in configured.items()}


def engine_overrides(engine: str) -> dict[str, object]:
    configured = engines()
    if engine not in configured:
        raise ValueError(
            f"unknown engine configuration '{engine}'; known: {', '.join(sorted(configured))}"
        )
    return configured[engine]


def runs_dir(engine: str = DEFAULT_ENGINE, brief_variant: str = REWRITTEN) -> Path:
    """One namespace per (engine configuration, brief variant). They never mix."""
    if brief_variant not in BRIEF_VARIANTS:
        raise ValueError(f"unknown brief variant: {brief_variant}")
    return DATA_DIR / "eval" / str(settings["eval.v4.runs_subdir"]) / engine / brief_variant


def stage_for(split: str) -> str:
    key = "eval.v4.validation_stage" if split == "validation" else "eval.v4.test_stage"
    return str(settings[key])


def v4_stages() -> list[str]:
    return [
        str(settings["eval.v4.rewrite_stage"]),
        stage_for("validation"),
        stage_for("test"),
    ]


def prompt_digest(name: str) -> str:
    import hashlib

    return hashlib.sha256((PROMPTS_DIR / f"{name}.md").read_bytes()).hexdigest()[:12]


def v4_config(split: str, engine: str, brief_variant: str) -> dict[str, object]:
    """The live configuration plus everything v4 can change about a run."""
    prompt = str(settings["llm.rerank_prompt"])
    return {
        **run_config(),
        "manifest_version": PACKAGE_MANIFEST_VERSION,
        "seed": int(settings["eval.v4.seed"]),
        "stage": stage_for(split),
        "benchmark_version": "v4",
        "engine_config": engine,
        "brief_variant": brief_variant,
        "rerank_prompt": prompt,
        "rerank_prompt_digest": prompt_digest(prompt),
        "rewrite_model": str(settings["eval.v4.rewrite_model"]),
        "rewrite_prompt_digest": prompt_digest(str(settings["eval.v4.rewrite_prompt"])),
    }


def cases_for(split: str, brief_variant: str = REWRITTEN) -> list[PackageManifestEntry]:
    return load_package_manifest(splits=(split,), brief_variant=brief_variant)


# ---------- spend control ----------

def project_case_cost() -> float:
    """Projected spend for one case under the *current* configuration.

    A package brief parses into more roles than a single-ticket brief did, and a role
    is a re-rank call, so the roles term is what decides whether a split fits the
    ceiling. The per-call figures come from ``eval.v4.projection`` and are rounded up:
    over-projecting can only refuse a split early, which is the safe direction.
    """
    projection = dict(settings["eval.v4.projection"])
    roles = float(projection["roles_per_case"])
    return float(projection["intent_call_usd"]) + roles * float(projection["rerank_call_usd"])


def enforce_v4_budget(pending_cases: int) -> float:
    """Refuse a split whose projected spend would break the owner's authorization.

    ``llm.max_stage_cost_usd`` is per stage, so it cannot see the rewrite stage and the
    two split stages together. This checks the projection against the logged spend of
    all three.
    """
    ceiling = float(settings["eval.v4.max_total_cost_usd"])
    projected = pending_cases * project_case_cost()
    spent = sum(cost for _, _, cost in spend_by_stage(v4_stages()))
    if spent + projected > ceiling:
        raise V4BudgetError(
            f"projected v4 spend ${spent + projected:.2f} (logged ${spent:.4f} + "
            f"projected ${projected:.2f} for {pending_cases} cases at "
            f"${project_case_cost():.4f}/case) exceeds the eval.v4.max_total_cost_usd "
            f"ceiling of ${ceiling:.2f} — escalate to the orchestrator before running "
            "this split"
        )
    return projected


# ---------- runs ----------

def run_v4_split(
    split: str,
    *,
    systems: Sequence[str] = ALL_SYSTEMS,
    engine: str = DEFAULT_ENGINE,
    brief_variant: str = REWRITTEN,
    limit: int | None = None,
) -> dict[str, int]:
    """Run one split of one engine configuration on one brief variant."""
    overrides = engine_overrides(engine)
    with settings.overridden(overrides):
        config = v4_config(split, engine, brief_variant)
        target = runs_dir(engine, brief_variant)
        cases = cases_for(split, brief_variant)
        if not cases:
            raise SystemExit(f"no {split} cases in the v4 manifest")
        if any(name in systems for name in GRAPH_SYSTEMS):
            done = load_checkpoint(split, runs_dir=target)
            pending = sum(
                1 for case in cases[:limit] if (CAPGRAPH_FULL, case.issue_id) not in done
            )
            projected = enforce_v4_budget(pending)
            print(
                f"pre-flight: {pending} unpaid cases at ${project_case_cost():.4f}/case, "
                f"projected ${projected:.2f}"
            )
        retrieval = config["retrieval"]
        print(
            f"benchmark v4 {split} [{engine}/{brief_variant}]: stage {config['stage']}, "
            f"prompt '{config['rerank_prompt']}' ({config['rerank_prompt_digest']}), "
            f"view {retrieval['rerank_candidate_view']}, window "
            f"{retrieval['rerank_top_k']}, bm25 arm {retrieval['bm25_top_k']}, "
            f"digest {config_digest(config)}"
        )
        return run_split(
            split,
            systems=systems,
            limit=limit,
            stage=str(config["stage"]),
            runs_dir=target,
            config=config,
            cases=cases,
            manifest_version=PACKAGE_MANIFEST_VERSION,
        )


def score(
    split: str,
    *,
    engine: str = DEFAULT_ENGINE,
    brief_variant: str = REWRITTEN,
    systems: Sequence[str] = ALL_SYSTEMS,
) -> tuple[list[EvaluationResult], dict[str, list[dict]]]:
    return score_split(
        split,
        systems,
        runs_dir=runs_dir(engine, brief_variant),
        manifest_cases=cases_for(split, brief_variant),
        manifest_version=PACKAGE_MANIFEST_VERSION,
    )


def per_case_metrics(
    split: str, system: str, *, engine: str = DEFAULT_ENGINE, brief_variant: str = REWRITTEN
) -> dict[str, dict[str, float]]:
    """Hit@K, Recall@K and reciprocal rank per case, from a checkpointed run."""
    cases = {case.issue_id: case for case in cases_for(split, brief_variant)}
    records = load_checkpoint(split, runs_dir=runs_dir(engine, brief_variant))
    out: dict[str, dict[str, float]] = {}
    for (name, issue_id), record in records.items():
        if name != system or "error" in record or issue_id not in cases:
            continue
        truth = set(cases[issue_id].truth_person_ids)
        ranked = list(record["ranked_ids"])
        out[issue_id] = {
            "hit_at_1": hit_at_k(ranked, truth, 1),
            "hit_at_5": hit_at_k(ranked, truth, 5),
            "hit_at_10": hit_at_k(ranked, truth, 10),
            "recall_at_5": recall_at_k(ranked, truth, 5),
            "recall_at_10": recall_at_k(ranked, truth, 10),
            "mrr": mrr(ranked, truth),
        }
    return out


def paired_rows(
    baseline: Mapping[str, Mapping[str, float]],
    variant: Mapping[str, Mapping[str, float]],
) -> list[str]:
    binary = [
        paired_binary(
            METRIC_LABELS[metric],
            {case: values[metric] for case, values in baseline.items()},
            {case: values[metric] for case, values in variant.items()},
        )
        for metric in ("hit_at_1", "hit_at_5", "hit_at_10")
    ]
    continuous = [
        paired_bootstrap(
            METRIC_LABELS[metric],
            {case: values[metric] for case, values in baseline.items()},
            {case: values[metric] for case, values in variant.items()},
        )
        for metric in ("recall_at_5", "recall_at_10", "mrr")
    ]
    return render_paired(binary, continuous)


# ---------- report ----------

def _by_system(results: Sequence[EvaluationResult]) -> dict[str, EvaluationResult]:
    return {result.system: result for result in results}


def manifest_section() -> list[str]:
    entries = load_package_manifest(splits=None)
    summary = manifest_summary(entries)
    selected = [entry for entry in entries if entry.split != "excluded"]
    truth_sizes = sorted(len(entry.truth_person_ids) for entry in selected)
    median_truth = truth_sizes[len(truth_sizes) // 2] if truth_sizes else 0
    lines = [
        "",
        "## The manifest",
        "",
        f"`{summary['version']}`, seed {settings['eval.v4.seed']}, "
        f"{summary['selected']} packages selected from {summary['candidates']} sprint "
        f"candidates: "
        + ", ".join(f"{split} {count}" for split, count in summary["splits"].items())
        + ". Per project: "
        + ", ".join(f"{key} {count}" for key, count in summary["projects"].items())
        + ".",
        "",
        "| Exclusion reason | Packages |",
        "|---|---:|",
        *(
            f"| `{reason}` | {count} |"
            for reason, count in summary["exclusion_reasons"].items()
        ),
        "",
        f"Truth sets hold {summary['truth_people_total']} person-slots across "
        f"{summary['selected']} cases (median {median_truth} people per package, range "
        f"{truth_sizes[0]}-{truth_sizes[-1]}). "
        f"{summary['truth_people_dropped_ineligible']} further people who resolved "
        "package issues were dropped because they were not eligible in the roster "
        "frozen at the cutoff — in v1 an ineligible truth person discarded the whole "
        "case (4,992 of them); here it narrows the truth set and is counted.",
        "",
        "| Truth-set size | Packages |",
        "|---|---:|",
        *(
            f"| {size} | {count} |"
            for size, count in sorted(summary["truth_set_sizes"].items())
        ),
    ]
    return lines


def headline_tables(split: str, engine: str, brief_variant: str = REWRITTEN) -> list[str]:
    results, failures = score(split, engine=engine, brief_variant=brief_variant)
    if not results:
        return []
    expected = len(cases_for(split, brief_variant))
    lines = [
        "",
        f"### {split} split — `{engine}`, {brief_variant} briefs",
        "",
    ]
    if CAPGRAPH_FULL not in {result.system for result in results}:
        lines += [
            "The two graph systems were **not run** in this arm — only the three "
            "offline baselines, which are free and identical across engine "
            "configurations. The unspent exposure of this split is reserved for the "
            "escalated `v2frozen` run (`docs/benchmark-v4-manifest.md` §6.4).",
            "",
        ]
    lines += [
        "| System | Cases in split | Scored | Failed |",
        "|---|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| `{result.system}` | {expected} | {result.n_briefs} | "
            f"{len(failures.get(result.system, ()))} |"
        )
    lines += [
        "",
        "| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | "
        "Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | "
        "Cost (USD) |",
        HEADER_RULE,
    ]
    for result in results:
        lines.append(summary_row(f"`{result.system}`", result))
    if failures:
        lines += ["", "| System | Package | Reason |", "|---|---|---|"]
        for system, broken in sorted(failures.items()):
            for failure in broken:
                lines.append(
                    f"| {system} | {failure['issue_key'] or failure['issue_id']} | "
                    f"{failure['error']} |"
                )
    diagnostics = run_diagnostics(split, runs_dir=runs_dir(engine, brief_variant))
    if diagnostics:
        lines += [
            "",
            f"{split} run diagnostics (`{engine}`, {brief_variant}):",
            "",
            "| Measure | Value |",
            "|---|---|",
            *(
                f"| {key.replace('_', ' ')} | "
                f"{', '.join(value) if isinstance(value, list) else value} |"
                for key, value in diagnostics.items()
            ),
        ]
    return lines


def per_project_table(split: str, engine: str) -> list[str]:
    results, _ = score(split, engine=engine)
    if not results:
        return []
    rosters = {
        case.project_key: len(case.eligible_roster) for case in cases_for(split)
    }
    lines = [
        "",
        f"### {split} split by project — `{engine}`",
        "",
        "| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | "
        "Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | "
        "Cost (USD) |",
        HEADER_RULE,
    ]
    for result in results:
        for project_key, value in result.per_project.items():
            lines.append(
                summary_row(
                    f"{result.system} / {project_key} (roster {rosters.get(project_key, 0)})",
                    value,
                )
            )
    return lines


def recall_divergence_section(split: str, engine: str = DEFAULT_ENGINE) -> list[str]:
    """Success test 1: Recall@K has to stop being a synonym for Hit@K."""
    results, _ = score(split, engine=engine)
    if not results:
        return []
    lines = [
        "",
        f"## Recall@K against Hit@K — {split} split, `{engine}`",
        "",
        "In v1-v3 each case had one truth person, so Recall@K *was* Hit@K by "
        "construction. Here a package has several, and the two answer different "
        "questions: Hit@K asks whether the shortlist found **anyone** who worked the "
        "package, Recall@K asks **what share** of them it found. The gap is the whole "
        "point of the rebuild.",
        "",
        "| System | N | Hit@5 | Recall@5 | Δ | Hit@10 | Recall@10 | Δ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| `{result.system}` | {result.n_briefs} | {result.hit_at_5:.3f} | "
            f"{result.recall_at_5:.3f} | {result.recall_at_5 - result.hit_at_5:+.3f} | "
            f"{result.hit_at_10:.3f} | {result.recall_at_10:.3f} | "
            f"{result.recall_at_10 - result.hit_at_10:+.3f} |"
        )
    return lines


def versus_bm25_section(split: str, engine: str = DEFAULT_ENGINE) -> list[str]:
    """Success test 2: does a staffing-shaped brief change the BM25 comparison?"""
    by_system = _by_system(score(split, engine=engine)[0])
    if CAPGRAPH_FULL not in by_system or "bm25" not in by_system:
        return []
    full, bm25 = by_system[CAPGRAPH_FULL], by_system["bm25"]
    metrics = ("hit_at_1", "hit_at_5", "hit_at_10", "recall_at_5", "recall_at_10", "mrr")
    deltas = {metric: getattr(full, metric) - getattr(bm25, metric) for metric in metrics}
    wins = sum(1 for value in deltas.values() if value > 0)
    verdict = (
        f"the graph system leads BM25 on {wins} of {len(metrics)} metrics"
        if wins > len(metrics) / 2
        else f"BM25 leads the graph system on {len(metrics) - wins} of {len(metrics)} metrics"
    )
    paired = paired_rows(
        per_case_metrics(split, "bm25", engine=engine),
        per_case_metrics(split, CAPGRAPH_FULL, engine=engine),
    )
    return [
        "",
        f"## The graph system against BM25 — {split} split, `{engine}`",
        "",
        "The v1 benchmark's single-ticket briefs were jargon-dense and narrow, which is "
        "the shape BM25 is best at; the backlog's hypothesis (G12) was that broader, "
        "staffing-shaped briefs would change that comparison. On this split "
        f"**{verdict}**. Reported in whichever direction it falls — a negative result "
        "here is a finding about the instrument, not a failure of it.",
        "",
        "| Metric | capgraph_full | bm25 | Δ |",
        "|---|---:|---:|---:|",
        *(
            f"| {METRIC_LABELS[metric]} | {getattr(full, metric):.3f} | "
            f"{getattr(bm25, metric):.3f} | {deltas[metric]:+.3f} |"
            for metric in metrics
        ),
        "",
        "BM25 is not automatically the bar to clear, though, and on multi-person truth "
        "it stops being the strongest baseline. Against the **best baseline on each "
        "metric** — chosen per column, not fixed in advance, so a weak result cannot "
        "hide behind a baseline it happens to beat:",
        "",
        "| Metric | capgraph_full | Best baseline | Δ |",
        "|---|---:|---|---:|",
        *(
            (
                lambda best: (
                    f"| {METRIC_LABELS[metric]} | {getattr(full, metric):.3f} | "
                    f"{getattr(best, metric):.3f} (`{best.system}`) | "
                    f"{getattr(full, metric) - getattr(best, metric):+.3f} |"
                )
            )(
                max(
                    (by_system[name] for name in BASELINE_SYSTEMS if name in by_system),
                    key=lambda result: (getattr(result, metric), result.system),
                )
            )
            for metric in metrics
        ),
        "",
        "Both arms answered the same cases, so the aggregate is not the whole evidence. "
        "Case by case, against BM25:",
        "",
        *paired,
    ]


def engine_comparison_section(split: str) -> list[str]:
    """v2-frozen against v3-frozen on the same cases, aggregate and paired."""
    v2 = _by_system(score(split, engine=V2_ENGINE)[0])
    v3 = _by_system(score(split, engine=DEFAULT_ENGINE)[0])
    # The baselines are identical across engine configurations by construction, so a
    # table without both graph systems would be a page of zeros pretending to be a
    # comparison. A split only one configuration was run on has no comparison to make.
    if not all(CAPGRAPH_FULL in results for results in (v2, v3)):
        return []
    # Only the graph systems can move: the baselines never read the engine
    # configuration, and their rows are verified identical rather than printed.
    identical = all(
        getattr(v2[name], metric) == getattr(v3[name], metric)
        for name in BASELINE_SYSTEMS
        if name in v2 and name in v3
        for metric in COMPARED
    )
    shared = [name for name in GRAPH_SYSTEMS if name in v2 and name in v3]
    lines = [
        "",
        f"## v2-frozen against v3-frozen — {split} split",
        "",
        "The open question the v3 report left behind: v3's retrieval and card view "
        "raised candidate recall but lost Hit@1 on single-ticket briefs. Broader briefs "
        "are exactly the case where the wider window and the lexical arm should pay off, "
        "so both frozen configurations were run over this manifest. Only the graph "
        "systems appear below: the three baselines never read the engine configuration, "
        "and their rows across the two arms are "
        + ("verified identical" if identical else "**NOT identical, which is a bug**")
        + ".",
        "",
        "| System | Metric | v2frozen | v3frozen | Δ |",
        "|---|---|---:|---:|---:|",
    ]
    for name in shared:
        for metric in COMPARED:
            before, after = getattr(v2[name], metric), getattr(v3[name], metric)
            if before is None or after is None:
                continue
            lines.append(
                f"| `{name}` | {METRIC_LABELS[metric]} | {before:.3f} | {after:.3f} | "
                f"{after - before:+.3f} |"
            )
    for system in GRAPH_SYSTEMS:
        baseline = per_case_metrics(split, system, engine=V2_ENGINE)
        variant = per_case_metrics(split, system, engine=DEFAULT_ENGINE)
        shared_cases = set(baseline) & set(variant)
        if not shared_cases:
            continue
        lines += [
            "",
            f"`{system}`, v3frozen against v2frozen on the {len(shared_cases)} cases "
            "both scored:",
            "",
            *paired_rows(baseline, variant),
        ]
    return lines


def rewrite_effect_section(split: str = "validation", engine: str = DEFAULT_ENGINE) -> list[str]:
    """What the rewrite itself did to the numbers, measured rather than assumed."""
    raw = _by_system(score(split, engine=engine, brief_variant=RAW)[0])
    rewritten = _by_system(score(split, engine=engine, brief_variant=REWRITTEN)[0])
    shared = [name for name in ALL_SYSTEMS if name in raw and name in rewritten]
    if not shared:
        return []
    entries = cases_for(split)
    raw_chars = sorted(len(entry.brief_raw) for entry in entries)
    rewritten_chars = sorted(len(entry.brief_rewritten) for entry in entries)
    lines = [
        "",
        f"## What the rewrite did — {split} split, `{engine}`",
        "",
        "Every case here has two briefs over the same as-of time, the same roster and "
        "the same truth: the raw package text (median "
        f"{raw_chars[len(raw_chars) // 2]:,} characters over "
        f"{sorted(entry.brief_issue_count for entry in entries)[len(entries) // 2]} "
        "issues) and the cheap-model rewrite of it (median "
        f"{rewritten_chars[len(rewritten_chars) // 2]:,} characters). The rewrite is "
        "what the benchmark uses, so its effect is measured here rather than assumed. "
        "A system that gains from the raw text is matching source vocabulary; one that "
        "holds up on the rewrite is matching the *description of the work*, which is "
        "the only thing a real staffing brief supplies.",
        "",
        "`most_active` is the control: it never reads the brief, and every one of its "
        "numbers is identical across the two variants"
        + (
            "."
            if all(
                getattr(raw["most_active"], metric) == getattr(rewritten["most_active"], metric)
                for metric in ("hit_at_1", "hit_at_5", "hit_at_10", "recall_at_5", "mrr")
            )
            else " — except it is **not**, which would be a bug."
        )
        + " Anything that moves, moved because the words changed.",
        "",
        "| System | Metric | raw | rewritten | Δ |",
        "|---|---|---:|---:|---:|",
    ]
    for name in shared:
        for metric in ("hit_at_1", "hit_at_5", "hit_at_10", "recall_at_5", "mrr"):
            before, after = getattr(raw[name], metric), getattr(rewritten[name], metric)
            if before is None or after is None:
                continue
            lines.append(
                f"| `{name}` | {METRIC_LABELS[metric]} | {before:.3f} | {after:.3f} | "
                f"{after - before:+.3f} |"
            )
    for system in GRAPH_SYSTEMS:
        baseline = per_case_metrics(split, system, engine=engine, brief_variant=RAW)
        variant = per_case_metrics(split, system, engine=engine, brief_variant=REWRITTEN)
        if not (set(baseline) & set(variant)):
            continue
        lines += [
            "",
            f"`{system}`, rewritten against raw, case by case:",
            "",
            *paired_rows(baseline, variant),
        ]
    lines += [
        "",
        "**The obvious objection, stated rather than buried.** The rewrite is written by "
        "a language model, and two of the five systems here contain language models, so "
        "a sceptic can ask whether the rewrite simply produces text that suits them. Two "
        "things bear on that. The deterministic `capgraph_score` arm never sees the "
        "brief inside a prompt after the intent parse — it is embedding similarity, term "
        "overlap and recency arithmetic — and it gains on the rewrite too, which is not "
        "what pure model-affinity would predict. But it does still depend on that one "
        "LLM intent parse, so the objection is narrowed rather than closed. What is not "
        "in question is the direction the raw variant flatters: on un-rewritten package "
        "text BM25 is the strongest system in this study, and that is precisely the "
        "artefact `G12` predicted a realistic brief would remove.",
    ]
    return lines


def caveats_section() -> list[str]:
    """What these numbers do not say. Generated where the figures are measured."""
    entries = load_package_manifest(splits=None)
    selected = [entry for entry in entries if entry.split != "excluded"]
    by_project: dict[str, list[PackageManifestEntry]] = {}
    for entry in selected:
        by_project.setdefault(entry.project_key, []).append(entry)
    overlaps: list[float] = []
    for group in by_project.values():
        ordered = sorted(group, key=lambda entry: entry.as_of_time or datetime.min)
        for before, after in zip(ordered, ordered[1:], strict=False):
            first, second = set(before.truth_person_ids), set(after.truth_person_ids)
            if first | second:
                overlaps.append(len(first & second) / len(first | second))
    capped = sum(1 for entry in selected if entry.brief_issues_omitted)
    dropped = sum(entry.truth_dropped_ineligible for entry in selected)
    kept = sum(len(entry.truth_person_ids) for entry in selected)
    return [
        "",
        "## Caveats specific to this instrument",
        "",
        "- **The target is still assignee prediction.** A package's truth set is the "
        "people who *did* the work, not the people who *should* have. Multi-person truth "
        "makes the label less arbitrary than v1's single name; it does not make it a "
        "statement about optimal staffing.",
        f"- **Cases are correlated.** Consecutive packages inside a project share a mean "
        f"Jaccard overlap of {sum(overlaps) / len(overlaps):.2f} in their truth sets "
        f"(n={len(overlaps)}), because the same team runs consecutive sprints. The "
        "effective sample size is smaller than the case count, which is why every "
        "comparison here is paired.",
        "- **`most_active` is structurally strong here**, in a way it was not on v1. When "
        "truth is a whole team, ranking people by raw volume captures a large share of it "
        "without reading the brief at all. Any claim about the graph system has to clear "
        "that baseline on Recall@K, not just BM25 on Hit@K.",
        "- **Four projects, not five.** EVG has no sprints in TAWOS, so it is absent. It "
        "had the smallest roster (21) and therefore the easiest Hit@10 in v1-v3.",
        f"- **{capped} of {len(selected)} briefs are capped** at 30 issues / 8,000 "
        "characters, so the brief is a sample of a large package rather than its whole "
        "content. The omitted issues still count toward truth, which makes those cases "
        "harder than they look.",
        f"- **Roster survivorship persists.** {dropped} people who resolved package "
        f"issues are not roster-eligible and were dropped from truth ({kept} remain). "
        "The system is never asked to name someone it has never seen, which reality does "
        "not guarantee.",
        "- **No v4-specific noise floor was measured.** The 0.100 run-to-run floor quoted "
        "in the v2 section was measured on the v1 instrument by re-running one "
        "configuration twice; nothing here re-establishes it for packages. Read the "
        "28-case validation deltas as directional only, and prefer the paired win/loss "
        "counts to the aggregates.",
    ]


def spend_section() -> list[str]:
    stages = v4_stages()
    lines = ["", "## Spend", "", "| Stage | Calls | Cost (USD) |", "|---|---:|---:|"]
    total = 0.0
    for stage, calls, cost in spend_by_stage(stages):
        total += cost
        lines.append(f"| `{stage}` | {calls} | {cost:.4f} |")
    lines.append(f"| **total** | | **{total:.4f}** |")
    purposes = spend_by_purpose(stages)
    if purposes:
        lines += [
            "",
            "| Call type | Calls | Cost (USD) |",
            "|---|---:|---:|",
            *(
                f"| `{name}` | {count} | {cost:.4f} |"
                for name, (count, cost) in purposes.items()
            ),
        ]
    lines += [
        "",
        "Reconciled against `data/llm_costs.jsonl` by stage name, retries included, "
        f"against the ${float(settings['eval.v4.max_total_cost_usd']):.2f} ceiling the "
        "owner authorized on 2026-08-14.",
    ]
    return lines


def render_report() -> str:
    """The v4 section: a new instrument, what it measured, and what it cost."""
    config = v4_config("test", DEFAULT_ENGINE, REWRITTEN)
    lines = [
        "# Benchmark v4 — work packages, multi-person truth",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} against manifest "
        f"`{PACKAGE_MANIFEST_VERSION}`, configuration digest `{config_digest(config)}`.",
        "",
        "**This is a different instrument, not a fourth tuning round. Nothing below is "
        "comparable to a v1-v3 row.** A v1 case was one issue, asked at its creation "
        "time, whose truth was the one person who resolved it. A v4 case is one **work "
        "package** — a sprint — asked at its recorded start date, whose brief is a "
        "cheap-model rewrite of the issues planned into it before it started, and whose "
        "truth is **everyone** who resolved any of its issues from that moment on. "
        "Different briefs, different labels, different cases, different projects in the "
        "mix. The v1-v3 sections above are untouched and stay quotable on their own "
        "terms.",
        "",
        "Grouping-unit verification, leakage guards, and the full exclusion accounting "
        "are in `docs/benchmark-v4-manifest.md`.",
        "",
        "## Configuration",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Grouping unit | {settings['eval.v4.grouping_unit']} (as-of = recorded sprint "
        "start) |",
        f"| Manifest | `{PACKAGE_MANIFEST_VERSION}`, seed {settings['eval.v4.seed']} |",
        f"| Brief rewrite | `{config['rewrite_model']}` "
        f"({config['rewrite_prompt_digest']}), frozen in the manifest |",
        "| Engine configurations | "
        + ", ".join(f"`{name}`" for name in sorted(engines()))
        + " |",
        f"| Intent / re-rank model | `{config['intent_model']}` / "
        f"`{config['rerank_model']}` |",
        f"| Embedding model | `{config['embedding_model']}` |",
        "| Cost-log stages | " + ", ".join(f"`{stage}`" for stage in v4_stages()) + " |",
    ]
    lines += manifest_section()
    for split in SPLITS:
        lines += recall_divergence_section(split)
        lines += versus_bm25_section(split)
    for split in SPLITS:
        for engine in sorted(engines()):
            lines += headline_tables(split, engine)
    for split in SPLITS:
        lines += engine_comparison_section(split)
    for split in SPLITS:
        lines += per_project_table(split, DEFAULT_ENGINE)
    lines += rewrite_effect_section()
    lines += caveats_section()
    lines += spend_section()
    return "\n".join(lines) + "\n"


def write_tracked_section(markdown: str, *, path: Path = TRACKED_REPORT) -> None:
    """Append (or replace) the v4 section below its marker, leaving v1-v3 alone."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    head = existing.split(V4_MARKER)[0].rstrip("\n")
    path.write_text(f"{head}\n\n{V4_MARKER}\n\n{markdown}", encoding="utf-8")


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark v4 runs and report")
    parser.add_argument("--split", choices=[*SPLITS, "all"], help="split to run")
    parser.add_argument("--engine", default=DEFAULT_ENGINE, help="engine configuration")
    parser.add_argument("--briefs", default=REWRITTEN, choices=list(BRIEF_VARIANTS))
    parser.add_argument("--systems", default=",".join(ALL_SYSTEMS))
    parser.add_argument("--baselines", action="store_true",
                        help="run only the three offline baselines")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", action="store_true",
                        help="offline: rebuild the v4 section of docs/eval-results.md")
    parser.add_argument("--summary", action="store_true",
                        help="offline: reconcile the manifest and exit")
    args = parser.parse_args(argv)

    if args.summary:
        print(json.dumps(manifest_summary(load_package_manifest(splits=None)), indent=2))
        return 0

    if args.split:
        systems = (
            ["bm25", "vector_only", "most_active"]
            if args.baselines
            else [name.strip() for name in args.systems.split(",") if name.strip()]
        )
        splits = SPLITS if args.split == "all" else (args.split,)
        for split in splits:
            counts = run_v4_split(
                split,
                systems=systems,
                engine=args.engine,
                brief_variant=args.briefs,
                limit=args.limit,
            )
            print(json.dumps(dict(sorted(counts.items())), indent=2))

    if args.report:
        markdown = render_report()
        V4_DIR.mkdir(parents=True, exist_ok=True)
        (V4_DIR / "results.md").write_text(markdown, encoding="utf-8")
        write_tracked_section(markdown)
        print(markdown)
        print(f"\nwrote {V4_DIR / 'results.md'} and the v4 section of {TRACKED_REPORT}")
        return 0

    if not args.split:
        parser.error("nothing to do: pass --split, --report, or --summary")
    return 0


__all__ = [
    "V4BudgetError",
    "cases_for",
    "engine_overrides",
    "engines",
    "enforce_v4_budget",
    "per_case_metrics",
    "project_case_cost",
    "render_report",
    "run_v4_split",
    "runs_dir",
    "score",
    "v4_config",
    "v4_stages",
    "write_tracked_section",
]


if __name__ == "__main__":
    raise SystemExit(main())
