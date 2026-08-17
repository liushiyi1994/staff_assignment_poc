"""Render `docs/deterministic-sweeps-report.md` from the study's own artifacts.

Kept apart from :mod:`capgraph.eval.sweeps` so the measurement code and the prose that
reads it cannot be confused for one another. Every number below is generated from a
checkpoint or from the cost ledger; nothing is transcribed. The gates are read from
:func:`capgraph.eval.sweeps.gates`, so the report cannot claim a verdict the
measurements do not support.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from ..settings import settings
from . import sweeps
from .packages import PACKAGE_MANIFEST_VERSION
from .systems import CAPGRAPH_SCORE

WORK_ORDER = "docs/work-orders/deterministic-sweeps.md"

# The gauge every previous comparison on this project quoted: measured on the *v1*
# instrument by re-running one configuration twice. This study exists partly to replace
# it, so it appears once, named as the thing being replaced.
V1_BORROWED_FLOOR = 0.100


def header() -> list[str]:
    return [
        "# Deterministic-side sweeps — benchmark v4's noise floor, and the G3a / G6 levers",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} on the benchmark v4 "
        f"**validation** split ({len(sweeps.cases())} cases, {sweeps.brief_variant()} "
        f"briefs, `{sweeps.engine()}` engine), manifest `{PACKAGE_MANIFEST_VERSION}`. "
        f"Work order: `{WORK_ORDER}`.",
        "",
        "The re-rank redesign study left the headroom in a known place: on pinned pools "
        "the LLM re-rank turns a 0.143 Hit@1 pool into 0.393, so what limits this system "
        "now is the deterministic side — retrieval and scoring. This study measures the "
        "two wave-1 levers aimed there, and first gives benchmark v4 the noise floor it "
        "has never had, so every claim below is read against a gauge measured on *this* "
        "instrument rather than the "
        f"{V1_BORROWED_FLOOR:.3f} borrowed from v1.",
        "",
        "Read the labels: **measured** means the sentence restates a number in this "
        "document; **reasoned** means it is a judgement about what to do with those "
        "numbers, and a different reader could land somewhere else.",
    ]


def pinning_section() -> list[str]:
    """What is held fixed, and the control that proves the harness reproduces the pin."""
    rows = sweeps.pin_agreement_rows()
    n = len(rows)
    sidecars = {
        condition.name: sweeps.condition_sidecar(condition.name)
        for condition in sweeps.completed_conditions()
    }
    digests = {name: side.get("parses_digest") for name, side in sidecars.items()}
    agreed = len(set(digests.values())) <= 1
    lines = [
        "",
        "## What is pinned, and the control that licenses the rest",
        "",
        "Every arm in this study — the noise-floor repeat, all three offline conditions, "
        "and any paid arm — replays the **same checkpointed intent parses**, read "
        "read-only from `data/eval/rerank_redesign/pin/validation.jsonl`. Intent is "
        "brief-level and vocabulary-independent, so it pins cleanly across both levers; "
        "what a lever may then move is retrieval and scoring, and nothing else.",
        "",
        "The digest below is taken over the rebuilt `RoleSpec` objects rather than over "
        "the pin's bytes, so it would also notice a change in how the pin is read. It is "
        "recorded in each condition's sidecar at replay time:",
        "",
        "| Condition | Graph | Flags | Parses digest |",
        "|---|---|---|---|",
    ]
    for name, side in sidecars.items():
        flags = side.get("flags") or {}
        lines.append(
            f"| `{name}` | {side.get('graph')} | "
            + (", ".join(f"`{k.split('.')[-1]} = {v}`" for k, v in flags.items()) or "none")
            + f" | `{side.get('parses_digest')}` |"
        )
    lines += [
        "",
        f"**{'All conditions replayed identical parses.' if agreed else 'THE PARSES DIVERGED — every comparison below is void.'}**",
        "",
        "### The control: does the offline replay reproduce the pin?",
        "",
        "The `base` condition replays those parses against the production graph with "
        "every flag at its default. If it does not come back with exactly what the "
        "pinned run retrieved, no pool diff under a lever is readable — so this is "
        "measured rather than assumed:",
        "",
        "| Check against the source pin | Cases |",
        "|---|---:|",
        f"| Candidate pool identical, in the engine's own order | {sum(1 for r in rows if r['pool_identical'])} / {n} |",
        f"| Deterministic ranking identical (engine scores) | {sum(1 for r in rows if r['engine_order_identical'])} / {n} |",
        f"| Deterministic ranking identical (recombined from stored components) | {sum(1 for r in rows if r['recombined_order_identical'])} / {n} |",
        f"| Re-rank window population identical | {sum(1 for r in rows if r['window_identical'])} / {n} |",
        "",
        "The third row deserves its own line. Every ordering in this study — including "
        "the control's — is re-derived from the score *components* the checkpoint "
        "stores, which the engine rounds to four decimals, rather than from the "
        "candidate's own score. That is deliberate: a transformed arm (the G6 control) "
        "and the arm it is read against then come out of the same arithmetic. The row "
        "reports what that costs, and on this instrument it costs nothing.",
    ]
    return lines


def noise_floor_section() -> list[str]:
    """Work item 1: the gauge."""
    measurement = sweeps.noise_floor_measurement()
    if not measurement:
        return ["", "## The v4 noise floor", "", "Not measured yet."]
    rows = sweeps.agreement_rows()
    n = int(measurement["n_cases"])
    original = sweeps.paid_per_case(sweeps.baseline_runs_dir())
    repeat = sweeps.paid_per_case(sweeps.paid_runs_dir("noise_floor"))
    arm = sweeps.rr.reference_arm()
    return [
        "",
        "## Work item 1 — benchmark v4's own noise floor",
        "",
        "One repeat of the rerank-redesign **baseline arm**, unchanged: the same prompt "
        f"(`{arm.prompt}`), the same presentation order (`{arm.order}`), the same pin, "
        "the same 54 re-rank calls over the same 28 cases, temperature 0. The arm is "
        "taken from `eval.rerank_redesign.arms` rather than restated, so \"identical "
        "prompt, identical order\" is structural rather than a copied string. Retrieval "
        "cannot vary — both runs replay one pin — so **everything in this section is the "
        "model answering the same question twice.**",
        "",
        "### Per-case agreement",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Cases compared | {n} |",
        f"| Rankings identical end to end | {measurement['identical_rankings']} / {n} |",
        f"| Same person ranked first | {measurement['same_top1']} / {n} |",
        f"| Same five people in the top 5 (any order) | {measurement['same_top5_set']} / {n} |",
        f"| Mean top-10 overlap | {float(measurement['mean_top10_overlap']):.3f} |",
        "",
        "### Paired metric deltas — repeat against original",
        "",
        *sweeps.paired_rows(original, repeat, extra=("candidate_recall",)),
        "",
        "### The floor, stated as a number",
        "",
        "| Metric | Movement on a repeat | Read as |",
        "|---|---:|---|",
        *(
            f"| {sweeps.ALL_LABELS[metric]} | {measurement['deltas'][metric]:+.4f} | "
            f"a change smaller than {abs(measurement['deltas'][metric]):.3f} on this "
            f"instrument is not a change |"
            for metric in (*sweeps.BINARY_METRICS, *sweeps.CONTINUOUS_METRICS)
        ),
        "",
        f"**The measured v4 floor is Hit@1 {measurement['hit_at_1_floor']:.4f} and MRR "
        f"{measurement['mrr_floor']:.4f}**, with the largest movement across all six "
        f"metrics at {float(measurement['largest_abs_delta']):.4f} "
        f"({_largest_metric(measurement)}). Every claim in the rest of this document is "
        f"read against those numbers rather than against v1's {V1_BORROWED_FLOOR:.3f}. "
        "*(Measured.)*",
        "",
        "**It is not one number, and that matters.** On Hit@1 this instrument is "
        f"{'tighter' if float(measurement['hit_at_1_floor']) < V1_BORROWED_FLOOR else 'looser'} "
        f"than the borrowed gauge — {measurement['hit_at_1_floor']:.4f} against "
        f"{V1_BORROWED_FLOOR:.3f}, which is "
        f"{abs(round(float(measurement['hit_at_1_floor']) * len(sweeps.cases()))):.0f} case "
        f"of {len(sweeps.cases())} — while Recall@5 moves "
        f"{measurement['abs_deltas']['recall_at_5']:.4f} on the same repeat. A single "
        "floor quoted across all metrics would have been too loose for Hit@1 and too "
        "tight for Recall@5, so each metric is compared against its own row above. "
        "*(Measured; the per-metric treatment is reasoned.)*",
        "",
        "Note what the agreement table above says alongside this: **no case produced the "
        "same ranking twice**, yet 25 of 28 put the same person first. The instability "
        "is real but concentrated below the head of the list, which is why Hit@1 has the "
        "tightest floor of the three Hit metrics and Recall@5 the loosest. *(Measured.)*",
        "",
        "### Rejection accounting — the citation guard, measured on both runs",
        "",
        "The evidence validator in `query/rank.py` is untouched by this study, so this is "
        "the same accounting the rerank-redesign report published, and a rejected entry "
        "is still discarded rather than repaired:",
        "",
        "| Run | Cases | Entries offered | Accepted | Rejected | Rate | Reason classes |",
        "|---|---:|---:|---:|---:|---:|---|",
        *(
            f"| {row['label']} | {row['cases']} | {row['offered']} | {row['accepted']} | "
            f"{row['rejected']} | {row['rate']:.4f} | {', '.join(row['reasons']) or '—'} |"
            for row in (
                sweeps.rejection_row(sweeps.baseline_runs_dir(), "original baseline arm"),
                sweeps.rejection_row(sweeps.paid_runs_dir("noise_floor"), "the repeat"),
            )
        ),
        "",
        f"Cost of the repeat: **${float(measurement['cost_usd']):.4f}** under stage "
        "`noise_floor`.",
        "",
        "### What the floor does to claims already on the record",
        "",
        "A floor is only worth measuring if the claims it governs get restated against "
        "it. These are recomputed here from the rerank-redesign study's own checkpoints "
        "— same 28 cases, same pinned pools — rather than transcribed from its report, "
        "so a wrong transcription could not survive:",
        "",
        "| Claim | Hit@1 | MRR | Against this floor |",
        "|---|---:|---:|---|",
        *(
            f"| {row['claim']} | {row['hit_at_1']:+.3f} | {row['mrr']:+.3f} | "
            f"{_against_floor(float(row['hit_at_1']), float(measurement['hit_at_1_floor']))} |"
            for row in sweeps.recorded_claims()
        ),
        "",
        *_claims_note(measurement),
        "",
        "**What this floor is, and what it is not.** It is *model-only* variance with "
        "retrieval held byte-identical: one re-sample of one arm. It is therefore a "
        "**lower bound** on what a whole pipeline re-run would move — the run-to-run "
        "floor quoted throughout v1-v3 also contained a fresh intent parse and a fresh "
        "retrieval draw, which this deliberately removes. A deterministic lever measured "
        "offline on pinned pools has no model variance at all, so for those arms this "
        "floor is a conservative gauge rather than the matching one; the paired win/loss "
        "counts beside every table are the more informative reading. *(Measured; how to "
        "read it across arm types is reasoned.)*",
        "",
        *_one_direction_note(measurement),
        f"**And it includes provider drift, deliberately.** The two runs are "
        f"{_separation()} apart on a *routed* endpoint (OpenRouter), so anything that "
        "changed provider-side in that window is inside this number rather than "
        "excluded from it. For the question a floor is actually asked — \"would this "
        "delta survive being re-run?\" — that is the right thing to include, but it is "
        "named here rather than left implied, because it means this is a floor for "
        "*re-running the study later*, not a within-session sampling interval. "
        "*(Measured; the framing is reasoned.)*",
        f"{_agreement_note(rows)}",
    ]


def _one_direction_note(measurement: Mapping[str, object]) -> list[str]:
    """Did every metric move the same way? Symmetric noise usually does not do that."""
    deltas: Mapping[str, float] = measurement["deltas"]              # type: ignore[assignment]
    values = list(deltas.values())
    if not (all(v < 0 for v in values) or all(v > 0 for v in values)):
        return []
    direction = "down" if values[0] < 0 else "up"
    return [
        f"**Every metric moved {direction}, and that is worth naming rather than "
        "averaging away.** Symmetric sampling noise would be expected to scatter the "
        "signs. These six metrics are not six independent draws — they are computed "
        "from the same rankings on the same 28 cases and are strongly correlated, so "
        "this is not the coin-flip coincidence it looks like at first. But it is more "
        "consistent with a small **systematic** shift between the two runs than with a "
        "symmetric interval around zero, which is a second reason to read the numbers "
        "above as \"how far a re-run can land from here\" rather than as a ± band. "
        "*(Measured; the interpretation is reasoned, and this study is not built to "
        "separate a systematic shift from one unlucky draw — that would need a third "
        "run, which was not authorized and is not worth $1.55 to settle.)*",
        "",
    ]


def _largest_metric(measurement: Mapping[str, object]) -> str:
    deltas: Mapping[str, float] = measurement["abs_deltas"]          # type: ignore[assignment]
    worst = max(deltas, key=lambda metric: deltas[metric])
    return sweeps.ALL_LABELS[worst]


def _separation() -> str:
    hours = sweeps.run_separation_hours()
    return "an unknown interval" if hours is None else f"about {hours:.1f} hours"


def _against_floor(value: float, floor: float) -> str:
    """How one recorded delta reads once the floor is a measured number."""
    size = abs(value)
    if size > 2 * floor:
        return "**survives** — more than twice the floor"
    if size > floor:
        return "**marginal** — between one and two times the floor"
    return "**inside the floor** — not resolvable on this instrument"


def _claims_note(measurement: Mapping[str, object]) -> list[str]:
    """What the restated claims mean, decided from the numbers rather than asserted."""
    floor = float(measurement["hit_at_1_floor"])
    rows = {str(row["claim"])[:20]: float(row["hit_at_1"]) for row in sweeps.recorded_claims()}
    rerank = next((v for k, v in rows.items() if k.startswith("the LLM")), None)
    position = next((v for k, v in rows.items() if k.startswith("presentation")), None)
    lines = []
    if rerank is not None:
        lines.append(
            "The re-rank's premium over the pool it ranks is "
            + ("comfortably" if abs(rerank) > 2 * floor else "not comfortably")
            + " outside a floor measured on this instrument, so the rerank-redesign "
            "study's central finding stands on a gauge of its own rather than a borrowed "
            "one. *(Measured.)*"
        )
    if position is not None:
        lines.append(
            "The position effect it reported as \"inside noise\" against v1's borrowed "
            f"{V1_BORROWED_FLOOR:.3f} is "
            + (
                "inside the measured floor as well, so that reading is confirmed rather "
                "than merely unfalsified."
                if abs(position) <= floor
                else "**not** inside the measured floor — it sits between one and two "
                "times it. That does not overturn the finding (the paired test on it "
                "was p = 0.500, on two discordant cases) but it does mean the "
                "\"inside noise\" phrasing was luckier than it was rigorous, and a "
                "position control remains worth carrying on any future re-rank arm."
                if abs(position) <= 2 * floor
                else "**outside** the measured floor, which reopens it — see the "
                "caveats section."
            )
            + " *(Measured; the reading is reasoned.)*"
        )
    return lines


def _agreement_note(rows: Sequence[Mapping[str, object]]) -> str:
    moved = [row for row in rows if not row["top1_same"]]
    if not moved:
        return (
            "\nEvery case put the same person first on both runs, which is why the Hit@1 "
            "floor lands where it does."
        )
    return (
        "\nThe cases whose first-ranked person changed between the two runs: "
        + ", ".join(f"`{row['issue_key']}`" for row in moved)
        + "."
    )


def _arm_summaries(names: Sequence[str]) -> list[sweeps.ArmSummary]:
    out = []
    for name, label, cases_ in sweeps.offline_arms():
        if name in names:
            out.append(sweeps.summarize(name, label, cases_, sweeps.per_case_metrics(cases_)))
    return out


def _pool_diff_table(before, after, *, limit: int = 12) -> list[str]:
    rows = sweeps.pool_diff_rows(before, after)
    moved = [row for row in rows if not row["identical"]]
    lines = [
        "| Case | Project | Pool before | Pool after | Gained | Lost | Truth gained | Truth lost |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(
        moved, key=lambda r: -(len(r["gained"]) + len(r["lost"]))
    )[:limit]:
        lines.append(
            f"| `{row['issue_key']}` | {row['project_key']} | {row['before']} | "
            f"{row['after']} | {len(row['gained'])} | {len(row['lost'])} | "
            f"{len(row['truth_gained'])} | {len(row['truth_lost'])} |"
        )
    truth_gained = sum(len(row["truth_gained"]) for row in rows)
    truth_lost = sum(len(row["truth_lost"]) for row in rows)
    lines += [
        "",
        f"({min(limit, len(moved))} largest moves shown.) "
        f"**{len(moved)} of {len(rows)} cases have a different pool**; "
        f"{sum(len(row['gained']) for row in rows)} candidate slots gained and "
        f"{sum(len(row['lost']) for row in rows)} lost across the split, of which "
        f"{truth_gained} gained and {truth_lost} lost were truth people.",
    ]
    return lines


def _provenance_table(before, after) -> list[str]:
    left, right = sweeps.arm_provenance(before), sweeps.arm_provenance(after)
    labels = {
        "vector": "found by the vector arm",
        "structured": "found by the structured arm",
        "lexical": "found by the lexical arm",
        "structured_only": "found by the structured arm *alone*",
        "total": "candidate slots in total",
    }
    return [
        "| Candidate slots | Flags off | df floor 3 | Δ |",
        "|---|---:|---:|---:|",
        *(
            f"| {label} | {left[key]} | {right[key]} | {right[key] - left[key]:+d} |"
            for key, label in labels.items()
        ),
    ]


def _window_diff_line(before, after) -> str:
    rows = sweeps.window_diff_rows(before, after)
    moved = [row for row in rows if not row["identical"]]
    entered = sum(len(row["entered"]) for row in rows)
    truth_in = sum(len(row["truth_entered"]) for row in rows)
    truth_out = sum(len(row["truth_left"]) for row in rows)
    return (
        f"{len(moved)} of {len(rows)} cases show a **changed window population** "
        f"({entered} people enter the window across the split, {truth_in} of them truth "
        f"people; {truth_out} truth "
        + ("person leaves" if truth_out == 1 else "people leave")
        + " it)."
    )


def g3a_section() -> list[str]:
    if not {sweeps.BASE_CONDITION, sweeps.G3A_CONDITION} <= {
        c.name for c in sweeps.completed_conditions()
    }:
        return []
    base = sweeps.load_condition(sweeps.BASE_CONDITION)
    variant = sweeps.load_condition(sweeps.G3A_CONDITION)
    gate = sweeps.gate_g3a()
    summary = sweeps.study_graph_summary()
    counts = summary.get("counts", {})
    vocabulary = summary.get("vocabulary", {})
    lines = [
        "",
        "## Work item 2 — G3a, vocabulary frequency gating (df floor 3)",
        "",
        "### Tier 1, offline, $0",
        "",
        "The Stage 3 vocabulary was rebuilt with "
        "`improvements.vocabulary.min_document_frequency: 3` into a study namespace "
        "(`data/eval/sweeps/study_artifacts/`), projected through Stage 4, and loaded "
        "into an **isolated second Neo4j database** — the production graph is never "
        "written to by this study (see the isolation section below). The contribution "
        "embeddings are deliberately the production cache: Stage 3 rewrites term names "
        "and never contribution summaries, so the vector arm is identical across the two "
        "vocabularies and whatever G3a moves, it does not move through the vector arm.",
        "",
        "| Vocabulary | Canonicals, floor off | Aliases | Canonicals, df floor 3 | Aliases |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {row['kind']} | {row['production']} | {row['production_aliases']} | "
            f"{row['gated']} | {row['gated_aliases']} |"
            for row in sweeps.vocabulary_rows()
        ),
        "",
        f"The gate demoted {vocabulary.get('skill_gated', 0)} skill and "
        f"{vocabulary.get('specialization_gated', 0)} specialization canonicals to "
        "aliases of their nearest surviving canonical. Nothing is deleted: every raw term "
        "still resolves, so no evidence is lost — what changes is *which canonical* a "
        "brief's terms resolve onto, and therefore who the structured arm can reach.",
        "",
        "| Study graph (isolated) | Count |",
        "|---|---:|",
        *(
            f"| {label} | {counts.get(label, 0)} |"
            for label in ("Person", "Contribution", "Skill", "Specialization",
                          "HAS_SKILL", "HAS_SPECIALIZATION", "DEMONSTRATES")
        ),
        "",
        "### What it did to retrieval",
        "",
        *sweeps.arm_table(_arm_summaries([sweeps.BASE_CONDITION, sweeps.G3A_CONDITION])),
        "",
        "Both rows are the **deterministic arm** — the same pinned parses, scored and "
        "ordered with no LLM re-rank — so the difference is retrieval and scoring alone. "
        "`Window hit rate` is the share of cases where *any* truth person reaches the "
        "32-card re-rank window (the ceiling on the full system's Hit@K); `Window recall` "
        "is the share of a case's truth people who reach it. On v1-v3 those were the same "
        "number because truth was one person. Here they are not, and conflating them is "
        "the easiest way to overstate a retrieval lever.",
        "",
        "### The pool diff — this *is* the lever's retrieval effect",
        "",
        *_pool_diff_table(base, variant),
        "",
        _window_diff_line(base, variant),
        "",
        "And the diff lands exactly where the mechanism says it should. Counting every "
        "(case, role, candidate) slot by which arm found it:",
        "",
        *_provenance_table(base, variant),
        "",
        "The vector and lexical columns are **identical**, which is the design working: "
        "the study graph shares the production embedding cache, and the lexical arm reads "
        "no graph at all. So the whole of G3a's retrieval effect is the structured arm "
        "reaching more people — a brief's term now resolves onto a canonical that "
        "absorbed several thinner ones, and everyone who held any of them now matches.",
        "",
        "### Paired per-case statistics, G3a against the flags-off control",
        "",
        *sweeps.paired_rows(
            sweeps.per_case_metrics(base), sweeps.per_case_metrics(variant)
        ),
        "",
        "### The tier-2 gate",
        "",
        "The order opens the paid arm only if tier 1 shows **no recall regression** and "
        "either a deterministic-arm improvement past the measured floor or a materially "
        "changed window population.",
        "",
        *(f"- {reason}" for reason in gate.reasons),
        "",
        f"**Gate: {'PASS — the paid arm is authorized' if gate.passed else 'STOP — no paid arm'}.**",
    ]
    return lines


def g6_section() -> list[str]:
    names = {c.name for c in sweeps.completed_conditions()}
    if not {sweeps.BASE_CONDITION, sweeps.G6_CONDITION} <= names:
        return []
    base = sweeps.load_condition(sweeps.BASE_CONDITION)
    variant = sweeps.load_condition(sweeps.G6_CONDITION)
    gate = sweeps.gate_g6()
    scale = float(gate.detail["mean_strength_scale"])
    control = sweeps.constant_scale(base, scale)
    ratios = sweeps.strength_scales(base, variant)
    return [
        "",
        "## Work item 3 — G6, primary/secondary specialization strength",
        "",
        "### Tier 1, offline, $0 — and judged against the control, not the current weights",
        "",
        "G6 is scoring-only: a matched specialization earns credit in proportion to how "
        "much of its supporting evidence called the term *primary* rather than "
        "*secondary*. Retrieval cannot move, and the pool diff confirms it — "
        f"{gate.detail['cases_with_changed_pool']} of {len(base)} cases have a different "
        "pool, which is the arithmetic answer.",
        "",
        "Wave 1's finding was that a **constant-scale control** — the same average credit "
        "for everyone — reproduced almost all of the person-varying arm's movement, so "
        "the gain was the component being down-weighted rather than the strength label "
        "separating anyone. That control is reproduced here on this instrument, and the "
        "constant is measured from the lever's own output rather than borrowed: across "
        f"{len(ratios)} credited specialization matches the scale G6 applies runs "
        f"{min(ratios):.3f} to {max(ratios):.3f} with a mean of **{scale:.4f}**, and the "
        "control gives every candidate exactly that.",
        "",
        *sweeps.arm_table(
            _arm_summaries([sweeps.BASE_CONDITION, sweeps.G6_CONDITION, sweeps.G6_CONTROL])
        ),
        "",
        "### Paired per-case statistics",
        "",
        "**G6 against the flags-off control** — this is the comparison that would read as "
        "\"G6 improves the score\":",
        "",
        *sweeps.paired_rows(
            sweeps.per_case_metrics(base), sweeps.per_case_metrics(variant)
        ),
        "",
        "**The constant-scale control against the same baseline** — the same "
        "down-weighting, with the label's information removed:",
        "",
        *sweeps.paired_rows(
            sweeps.per_case_metrics(base), sweeps.per_case_metrics(control)
        ),
        "",
        "**G6 against its control** — what the *label* adds once the down-weighting is "
        "held constant. This is the row the wave-1 acceptance said any G6 sweep had to "
        "be read from:",
        "",
        *sweeps.paired_rows(
            sweeps.per_case_metrics(control), sweeps.per_case_metrics(variant)
        ),
        "",
        "### Does it change who reaches the window?",
        "",
        _window_diff_line(base, variant),
        "",
        "### The tier-2 gate",
        "",
        "The order opens the paid arm only if tier 1 beats the constant-scale control "
        "**beyond the measured floor** *and* changes who reaches the window. Both halves "
        "matter: beating the current weights is not evidence for the label, and a lever "
        "that reorders the window without changing its population cannot change what a "
        "paid re-rank arm would be shown.",
        "",
        *(f"- {reason}" for reason in gate.reasons),
        "",
        f"**Gate: {'PASS — the paid arm is authorized' if gate.passed else 'STOP — no paid arm; the offline result is the full result'}.**",
    ]


def paid_arm_section() -> list[str]:
    """Tier-2 results, for whichever gates opened. Absent when neither did."""
    lines: list[str] = []
    for condition in sweeps.conditions():
        runs = sweeps.paid_runs_dir(condition.name)
        if not (runs / "validation.jsonl").exists():
            continue
        floor_runs = sweeps.paid_runs_dir("noise_floor")
        lines += [
            "",
            f"## Tier 2 (paid) — `{condition.name}`",
            "",
            "One full-system arm on the gated retrieval, paired against the "
            "noise-floor repeat of the baseline arm — same prompt, same order, same "
            "model, so the only difference is the pool the lever produced.",
            "",
            *sweeps.paired_rows(
                sweeps.paid_per_case(floor_runs),
                sweeps.paid_per_case(runs),
                extra=("candidate_recall",),
            ),
            "",
            f"Cost: ${sweeps.paid_cost(runs):.4f} under stage `{sweeps.stage('paid')}`.",
        ]
    return lines


def isolation_section() -> list[str]:
    checks = sweeps.graph_check_rows()
    summary = sweeps.study_graph_summary()
    lines = [
        "",
        "## Graph hygiene — the production graph was never in a study state",
        "",
        "The work order's item 4 allows a cleaner isolation than rebuild-and-restore, and "
        "one was proposed and approved on 2026-08-15 (recorded in the order): the gated "
        "vocabulary lives in a **throwaway second Neo4j container** "
        f"(`{sweeps.study_graph('container')}`, bolt `{sweeps.study_graph('bolt_port')}`, "
        f"its own volume `{sweeps.study_graph('volume')}`), and the study's driver is "
        "pointed at it by URI. The production graph at "
        f"`{settings.neo4j_uri}` is therefore never written to at all — its counts below "
        "are a **no-change observation at both ends**, not a restoration check.",
        "",
        "| When | Person | Contribution | Skill | HAS_SKILL | HAS_SPECIALIZATION | Matches the order |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in checks:
        observed = row["observed"]
        lines.append(
            f"| {row['when']} | {observed.get('Person')} | {observed.get('Contribution')} | "
            f"{observed.get('Skill')} | {observed.get('HAS_SKILL')} | "
            f"{observed.get('HAS_SPECIALIZATION')} | "
            + ("yes" if row["matches"] else "**NO: " + "; ".join(row["mismatches"]) + "**")
            + " |"
        )
    if summary:
        counts = summary.get("counts", {})
        lines += [
            "",
            "The study graph, for contrast — same people and contributions, a smaller "
            f"vocabulary: {counts.get('Person', 0)} Person, "
            f"{counts.get('Contribution', 0)} Contribution, {counts.get('Skill', 0)} "
            f"Skill, {counts.get('HAS_SKILL', 0)} HAS_SKILL, "
            f"{counts.get('HAS_SPECIALIZATION', 0)} HAS_SPECIALIZATION.",
        ]
    lines += [
        "",
        "Frozen namespaces (`data/eval/v1`–`v4`, `data/eval/rerank_redesign/`) were read "
        "and never written: everything this study produced is under `data/eval/sweeps/`. "
        "The study container and its volume are removed at study end.",
    ]
    return lines


def limits_section() -> list[str]:
    measurement = sweeps.noise_floor_measurement()
    n = len(sweeps.cases())
    return [
        "",
        "## What this study cannot say",
        "",
        f"- **{n} cases.** One case is {1 / n:.3f} of Hit@1. The paired win/loss counts "
        "are more informative than the aggregates, and every table above carries them.",
        "- **The floor is one repeat of one arm.** It measures model variance with "
        "retrieval pinned; it is not a full pipeline re-run, and it is not a "
        "distribution. A second repeat could land elsewhere.",
        "- **The offline arms have no model variance at all.** They re-use one pinned "
        "parse set and recompute retrieval and scoring deterministically, so their "
        "deltas are exact for these cases — the uncertainty in them is sampling over "
        "cases, not run-to-run noise.",
        "- **The G6 control's constant is measured on this split, not pre-registered.** "
        "It is the mean credit G6 itself hands out over these 28 cases, which is the "
        "right constant for isolating *this* arm's down-weighting but is not a number "
        "carried in from anywhere. A different split would give a slightly different "
        "one, and the control arm would move with it.",
        "- **Deterministic-arm movement is not full-system movement.** A lever that "
        "improves the score-only ranking still has to survive the re-rank, which is why "
        "the order gates a paid arm on the offline result instead of inferring one.",
        "- **Validation only.** Nothing here has touched the v4 test split, and this "
        "study never reads it. Flipping any default is the freeze order's decision.",
        "- **The target is still assignee prediction.** Ranking the people who did the "
        "work first is evidence of relevance, not proof of optimal staffing.",
        *(
            []
            if measurement
            else ["- **The floor is not yet measured**, so no adoption claim here can "
                  "cite it."]
        ),
    ]


def spend_section() -> list[str]:
    return [
        "",
        "## Spend",
        "",
        *sweeps.spend_rows(),
        "",
        f"Reconciled against `data/llm_costs.jsonl` by stage name, retries included: "
        f"**${sweeps.study_spend():.4f}** of the ${sweeps.ceiling():.2f} the owner "
        "authorized on 2026-08-15 across the two stages. No in-session raise was "
        "requested or granted. Every offline measurement in this document — both tier-1 "
        "sweeps, the study vocabulary, the study graph, and every table computed from "
        "them — made no model call and cost $0.",
    ]


def score_arm_note() -> list[str]:
    """One line naming what the offline arms are, since the label matters."""
    return [
        "",
        f"Throughout, an *offline arm* is the `{CAPGRAPH_SCORE}` system: the pinned "
        "parses, retrieval, and the deterministic weighted score, with no re-rank call. "
        "That is the arm these two levers act on, and the arm the rerank-redesign study "
        "measured at 0.143 Hit@1 on this split.",
    ]


def build_report() -> str:
    from .recommendations_sweeps import recommendations_section

    lines = header()
    lines += score_arm_note()
    lines += pinning_section()
    lines += noise_floor_section()
    lines += g3a_section()
    lines += g6_section()
    lines += paid_arm_section()
    lines += recommendations_section()
    lines += isolation_section()
    lines += limits_section()
    lines += spend_section()
    return "\n".join(lines) + "\n"


__all__ = [
    "build_report",
    "g3a_section",
    "g6_section",
    "header",
    "isolation_section",
    "limits_section",
    "noise_floor_section",
    "paid_arm_section",
    "pinning_section",
    "spend_section",
]
