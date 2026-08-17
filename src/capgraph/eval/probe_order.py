"""Backlog G7: is the LLM re-rank ranking, or following the order it was handed?

    uv run python -m capgraph.eval.probe_order --run       # SPENDS, ceiling $2
    uv run python -m capgraph.eval.probe_order --report    # offline

With ``retrieval.rerank_samples`` at 1 — the adopted v3 configuration — candidates reach
the re-rank in deterministic score order, best first. That is an intentional prior and it
has never been ablated, which leaves two readings of benchmark v2's finding that a better
input ordering produced the same output ranking:

* the re-rank is substantially re-expressing the deterministic score, or
* the re-rank is substantially following presentation order.

This probe runs the same 30 validation cases under the same frozen v3 configuration with
one thing changed — the window is presented worst-first — and pairs the result against
the frozen ``ab_window32`` arm case by case. Per the backlog's success test: a Hit@1 move
beyond the measured 0.100 run-to-run floor means presentation order dominates and the
re-rank prompt needs rethinking; a move inside it means the score-order prior is doing
its job and v2's reading stands.

**Spend discipline.** The probe writes to its own cost-log stage (``probe_order``) and
its own checkpoint namespace, and it is checked against its own ceiling — not
``llm.max_stage_cost_usd``, which at $25 could not enforce a $2 authorization. The
ceiling is re-checked before every chunk of cases rather than once up front, so an
unexpected per-case cost stops the run near the limit instead of past it.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from .. import improvements
from ..llm import cost_log_path
from ..settings import DATA_DIR, settings
from .metrics import load_manifest
from .paired import paired_binary, paired_bootstrap, render_paired
from .run_eval import config_digest, load_checkpoint, run_split, score_split
from .run_v3 import METRIC_LABELS, per_case_metrics, project_case_cost, runs_dir, v3_config
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE

FLAG = improvements.FLAG_ORDER
SPLIT = "validation"
SYSTEMS = (CAPGRAPH_FULL, CAPGRAPH_SCORE)
BINARY_METRICS = ("hit_at_1", "hit_at_5", "hit_at_10")

# The v2 section measured this by re-running one configuration unchanged, and every v3
# lever was read against it. The backlog's success test for G7 is stated in these terms.
RUN_TO_RUN_FLOOR = 0.100

REPORT_PATH = DATA_DIR / "wave1" / "probe_order.json"


class ProbeBudgetError(RuntimeError):
    """The probe's own ceiling, checked before every chunk of cases."""


def probe_setting(name: str, default=None):
    return settings.get(f"improvements.probe_order.{name}", default)


def probe_stage() -> str:
    return str(probe_setting("stage", "probe_order"))


def probe_runs_dir() -> Path:
    return DATA_DIR / "eval" / str(probe_setting("runs_subdir", "probe/order"))


def baseline_dir() -> Path:
    """The frozen forward-order arm the probe is paired against."""
    return runs_dir(str(probe_setting("baseline_variant", "ab_window32")))


def probe_config() -> dict[str, object]:
    """The frozen v3 validation configuration, under the probe's stage and flag.

    Built inside the flag override, so the recorded configuration says the run was made
    with the order reversed and its digest differs from the frozen arm's. Two runs that
    presented their candidates differently can then never be appended to one checkpoint.
    """
    config = v3_config(SPLIT)
    config["stage"] = probe_stage()
    return config


def probe_spend() -> float:
    """Logged spend under the probe's stage, read from the ledger rather than tallied."""
    path = cost_log_path()
    if not path.exists():
        return 0.0
    stage = probe_stage()
    total = 0.0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                if str(record.get("stage")) == stage:
                    total += float(record["cost_usd"])
    return round(total, 6)


def enforce_probe_budget(pending_cases: int) -> float:
    """Refuse the next chunk when it would break the owner's $2 authorization."""
    ceiling = float(probe_setting("max_total_cost_usd", 2.0))
    projected = pending_cases * project_case_cost()
    spent = probe_spend()
    if spent + projected > ceiling:
        raise ProbeBudgetError(
            f"projected probe spend ${spent + projected:.2f} (logged ${spent:.4f} + "
            f"projected ${projected:.2f} for {pending_cases} cases at "
            f"${project_case_cost():.4f}/case) exceeds the "
            f"improvements.probe_order.max_total_cost_usd ceiling of ${ceiling:.2f} — "
            "escalate to the orchestrator before running more of this probe"
        )
    return projected


def pending_cases(target: Path) -> int:
    """Cases the probe has not yet paid for, from its own checkpoint."""
    done = load_checkpoint(SPLIT, runs_dir=target)
    return sum(
        1
        for case in load_manifest(splits=(SPLIT,))
        if (CAPGRAPH_FULL, case.issue_id) not in done
    )


def run_probe(*, limit: int | None = None, target: Path | None = None) -> dict[str, int]:
    """Run the reversed-order arm over the validation split, chunk by chunk.

    ``run_split`` resumes from its checkpoint, so running it repeatedly with a widening
    ``limit`` walks the split in chunks while the ceiling is re-checked between them.
    """
    target = probe_runs_dir() if target is None else target
    chunk = max(1, int(probe_setting("chunk_size", 5)))
    with improvements.overridden({FLAG: improvements.ORDER_REVERSE}):
        config = probe_config()
        total = len(load_manifest(splits=(SPLIT,)))
        total = total if limit is None else min(total, limit)
        print(
            f"G7 probe: {total} {SPLIT} cases, presentation order "
            f"'{improvements.rerank_presentation_order()}', stage '{config['stage']}', "
            f"digest {config_digest(config)}, ceiling "
            f"${float(probe_setting('max_total_cost_usd', 2.0)):.2f}"
        )
        counts = {"cases": 0, "skipped": 0, "ran": 0, "failed": 0}
        for upper in range(chunk, total + chunk, chunk):
            upper = min(upper, total)
            outstanding = pending_cases(target)
            if outstanding == 0:
                break
            projected = enforce_probe_budget(min(chunk, outstanding))
            print(
                f"  chunk to case {upper}: logged ${probe_spend():.4f}, "
                f"projected ${projected:.2f} for this chunk"
            )
            for key, value in run_split(
                SPLIT,
                systems=SYSTEMS,
                limit=upper,
                stage=str(config["stage"]),
                runs_dir=target,
                config=config,
            ).items():
                counts[key] = value if key == "cases" else counts[key] + value
        print(f"probe spend: ${probe_spend():.4f}")
        return counts


# ---------- reporting ----------

def aggregate_rows(target: Path | None = None) -> list[str]:
    """The probe beside the forward-order arm it is paired with."""
    target = probe_runs_dir() if target is None else target
    lines = [
        "| Arm | System | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | Cost (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, path in (
        ("`ab_window32` (score order, frozen)", baseline_dir()),
        ("`probe_order` (reverse order)", target),
    ):
        for result in score_split(SPLIT, runs_dir=path)[0]:
            if result.system not in SYSTEMS:
                continue
            recall = (
                "N/A" if result.candidate_recall is None else f"{result.candidate_recall:.3f}"
            )
            lines.append(
                f"| {label} | `{result.system}` | {result.n_briefs} | "
                f"{result.hit_at_1:.3f} | {result.hit_at_5:.3f} | {result.hit_at_10:.3f} | "
                f"{result.mrr:.3f} | {recall} | {result.cost_usd_total:.4f} |"
            )
    return lines


def paired_tables(target: Path | None = None) -> tuple[list[str], dict[str, dict]]:
    """Paired per-case statistics for both systems, plus the deltas as data."""
    target = probe_runs_dir() if target is None else target
    lines: list[str] = []
    deltas: dict[str, dict] = {}
    for system in SYSTEMS:
        before = per_case_metrics(SPLIT, system, runs_dir_=baseline_dir())
        after = per_case_metrics(SPLIT, system, runs_dir_=target)
        shared = set(before) & set(after)
        if not shared:
            continue
        binary = [
            paired_binary(
                METRIC_LABELS[metric],
                {case: values[metric] for case, values in before.items()},
                {case: values[metric] for case, values in after.items()},
            )
            for metric in BINARY_METRICS
        ]
        continuous = paired_bootstrap(
            "MRR",
            {case: values["mrr"] for case, values in before.items()},
            {case: values["mrr"] for case, values in after.items()},
        )
        deltas[system] = {
            **{row.metric: row.delta for row in binary},
            "MRR": continuous.mean_delta,
            "n": len(shared),
            "wins_hit_at_1": binary[0].wins,
            "losses_hit_at_1": binary[0].losses,
            "p_hit_at_1": binary[0].p_value,
        }
        lines += [
            "",
            f"`{system}`, reverse order against score order on the {len(shared)} cases "
            "both arms scored:",
            "",
            *render_paired(binary, [continuous]),
        ]
    return lines, deltas


def verdict(deltas: Mapping[str, Mapping[str, float]]) -> str:
    """The backlog's success test, applied to the measured Hit@1 move."""
    full = deltas.get(CAPGRAPH_FULL)
    if not full:
        return "The probe produced no comparable cases, so it decides nothing."
    move = abs(float(full["Hit@1"]))
    gauge = abs(float(deltas.get(CAPGRAPH_SCORE, {}).get("Hit@1", 0.0)))
    if move > RUN_TO_RUN_FLOOR:
        return (
            f"Hit@1 moved {full['Hit@1']:+.3f}, **beyond** the {RUN_TO_RUN_FLOOR:.3f} "
            "run-to-run floor: presentation order is doing enough of the ordering that "
            "the re-rank prompt needs rethinking before any further re-rank tuning is "
            "worth paying for."
        )
    return (
        f"Hit@1 moved {full['Hit@1']:+.3f}, **inside** the {RUN_TO_RUN_FLOOR:.3f} "
        "run-to-run floor, against a deterministic-arm gauge of "
        f"{gauge:.3f} on the same comparison. Reversing the presentation order did not "
        "move the answer further than re-running the pipeline unchanged does, so the "
        "score-order prior is doing its job and benchmark v2's reading — that the "
        "re-rank is largely re-expressing the deterministic score — stands."
    )


def render_report(target: Path | None = None) -> str:
    target = probe_runs_dir() if target is None else target
    paired, deltas = paired_tables(target)
    spent = probe_spend()
    return "\n".join(
        [
            "# G7 — re-rank presentation-order probe",
            "",
            "One paid arm: the frozen v3 configuration on the 30 validation cases, with "
            "the re-rank window presented worst-first instead of best-first. Everything "
            "else — retrieval, weights, prompt, window width, model — is identical to "
            f"`{probe_setting('baseline_variant', 'ab_window32')}`, which is the same "
            "configuration presented best-first.",
            "",
            "The `capgraph_score` rows are the in-study noise gauge: the deterministic "
            "arm never sees a prompt or an ordering, so whatever it moves by between "
            "these two runs is run-to-run variance and nothing else.",
            "",
            *aggregate_rows(target),
            "",
            "## Paired per-query statistics",
            *paired,
            "",
            "## Verdict",
            "",
            verdict(deltas),
            "",
            "## Spend",
            "",
            f"`{probe_stage()}` logged **${spent:.4f}** against the "
            f"${float(probe_setting('max_total_cost_usd', 2.0)):.2f} ceiling the owner "
            "authorized on 2026-08-14, reconciled against `data/llm_costs.jsonl` by "
            "stage name with retries included.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backlog G7 presentation-order probe")
    parser.add_argument("--run", action="store_true", help="SPENDS: run the reversed arm")
    parser.add_argument("--limit", type=int, help="run only the first N cases")
    parser.add_argument("--report", action="store_true", help="offline: tables and verdict")
    args = parser.parse_args(argv)
    if not (args.run or args.report):
        parser.error("nothing to do: pass --run (spends) or --report (offline)")

    if args.run:
        counts = run_probe(limit=args.limit)
        print(json.dumps(dict(sorted(counts.items())), indent=2))

    if args.report or args.run:
        markdown = render_report()
        print(markdown)
        _, deltas = paired_tables()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(
                {
                    "stage": probe_stage(),
                    "baseline_variant": str(probe_setting("baseline_variant")),
                    "run_to_run_floor": RUN_TO_RUN_FLOOR,
                    "spend_usd": probe_spend(),
                    "deltas": deltas,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {REPORT_PATH}")
    return 0


__all__: Sequence[str] = [
    "FLAG",
    "ProbeBudgetError",
    "aggregate_rows",
    "baseline_dir",
    "enforce_probe_budget",
    "paired_tables",
    "probe_config",
    "probe_runs_dir",
    "probe_spend",
    "probe_stage",
    "render_report",
    "run_probe",
    "verdict",
]


if __name__ == "__main__":
    raise SystemExit(main())
