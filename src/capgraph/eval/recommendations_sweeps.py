"""The per-lever recommendations, written to the measurements and checked against them.

Separated from :mod:`capgraph.eval.report_sweeps` because it is the one part of the
report that is a *judgement* rather than a rendering. The numbers in the prose are
pulled live from the checkpoints, but the reading of them is written by hand, for the
outcome that was actually measured.

That creates a hazard the rest of the report does not have: if someone re-runs a
condition and the result flips, hand-written prose would keep asserting the old reading
over new numbers. :func:`_guard` closes it — every recommendation states the gate
outcome it was written for, and the section prints a loud banner instead of the
recommendation if the measurements no longer agree.
"""
from __future__ import annotations

from collections.abc import Sequence

from . import sweeps

# The gate outcome each recommendation below was written against. False means "tier 1
# stopped the lever"; a flip to True invalidates the prose, not just the number in it.
WRITTEN_FOR = {"G3a": False, "G6": False}


def _guard(gate: sweeps.Gate) -> list[str]:
    expected = WRITTEN_FOR.get(gate.lever)
    if expected is None or expected == gate.passed:
        return []
    return [
        "",
        f"> **The measurements moved.** The recommendation below was written for a "
        f"`{gate.lever}` tier-1 gate that "
        + ("stopped" if expected is False else "passed")
        + f", and the gate now {'passes' if gate.passed else 'stops'}. Re-read the "
        "tables and rewrite this section before quoting it.",
    ]


def _floor_line() -> str:
    hit = sweeps.measured_floor("hit_at_1")
    mrr = sweeps.measured_floor("mrr")
    if hit is None:
        return "the v4 floor has not been measured"
    return f"the measured v4 floor (Hit@1 {hit:.4f}, MRR {mrr:.4f})"


def _grain() -> str:
    """One case, as a share of Hit@1. The other yardstick, and the honest one offline."""
    n = len(sweeps.cases())
    return f"{1 / n:.3f} (one case in {n})"


def _cases(value: float) -> str:
    """A Hit@K delta restated as what it actually is at this sample size: N cases."""
    n = len(sweeps.cases())
    return f"{abs(round(value * n)):.0f} case{'' if abs(round(value * n)) == 1 else 's'}"


def _arm(name: str) -> sweeps.ArmSummary:
    for arm_name, label, cases_ in sweeps.offline_arms():
        if arm_name == name:
            return sweeps.summarize(arm_name, label, cases_, sweeps.per_case_metrics(cases_))
    raise KeyError(f"no offline arm '{name}'")


def g3a_recommendation() -> list[str]:
    gate = sweeps.gate_g3a()
    base, variant = _arm(sweeps.BASE_CONDITION), _arm(sweeps.G3A_CONDITION)
    detail = gate.detail
    pool_rows = sweeps.pool_diff_rows(
        sweeps.load_condition(sweeps.BASE_CONDITION),
        sweeps.load_condition(sweeps.G3A_CONDITION),
    )
    window_rows = sweeps.window_diff_rows(
        sweeps.load_condition(sweeps.BASE_CONDITION),
        sweeps.load_condition(sweeps.G3A_CONDITION),
    )
    lost = [row for row in window_rows if row["truth_left"]]
    gained = [row for row in pool_rows if row["truth_gained"]]
    return [
        *_guard(gate),
        "",
        "### G3a — document-frequency floor 3: **close**",
        "",
        "**It is not cosmetic, and that is the first thing to say.** The gate takes the "
        f"skill vocabulary from {sweeps.vocabulary_rows()[0]['production']} canonicals to "
        f"{sweeps.vocabulary_rows()[0]['gated']} and moves the candidate pool on "
        f"{detail['cases_with_changed_pool']} of {len(pool_rows)} cases and the re-rank "
        f"window population on {detail['cases_with_changed_window']} of "
        f"{len(window_rows)}. The backlog's own success test — \"a smaller vocabulary "
        "that does not improve retrieval is cosmetic\" — is answered: it changes "
        "retrieval a great deal. *(Measured.)*",
        "",
        "**Every directional signal it produces points the wrong way at the head of the "
        f"list.** On the deterministic arm, Hit@1 falls {base.hit_at_1:.3f} → "
        f"{variant.hit_at_1:.3f} ({detail['hit_at_1_delta']:+.3f}) and MRR "
        f"{base.mrr:.3f} → {variant.mrr:.3f} ({detail['mrr_delta']:+.3f}). Be exact "
        f"about what that is worth: these are offline arms with **no model variance at "
        "all** — the same pinned parses, recomputed deterministically — so the movement "
        f"is exact for these cases, but it is {_cases(detail['hit_at_1_delta'])} of "
        f"Hit@1 at a sampling grain of {_grain()}. The claim these numbers support is "
        "\"G3a does not improve the deterministic ranking\", which is what the gate "
        "needed to know; the claim they do **not** support is a precise size for the "
        "harm. *(Measured; the distinction is reasoned.)*",
        "",
        "**The mechanism is visible in the pool sizes, and it is the gate's own logic "
        "turned against it.** Folding a thin canonical into its nearest surviving "
        "canonical means a brief's term now resolves onto a term that many more people "
        f"hold, so the structured arm matches more people less specifically: the mean "
        f"pool grows {base.pool_mean:.1f} → {variant.pool_mean:.1f}. Candidate recall "
        f"does rise ({base.candidate_recall:.3f} → {variant.candidate_recall:.3f}; "
        + (
            f"{sum(len(row['truth_gained']) for row in gained)} truth people entered the "
            f"pool, on {len(gained)} case{'' if len(gained) == 1 else 's'})"
            if gained
            else "no truth person entered the pool)"
        )
        + ", and Recall@10 rises with it — the gate genuinely finds people the ungated "
        "vocabulary missed. But it buys that in the tail and pays for it at rank 1. "
        "*(Measured.)*",
        "",
        "**The gate stops it on the recall guard, and the guard is doing real work here, "
        "not tripping on a technicality.** Candidate recall improved, but *window* hit "
        f"rate fell {base.window_hit:.3f} → {variant.window_hit:.3f}: "
        + (
            "on "
            + ", ".join(f"`{row['issue_key']}`" for row in lost)
            + " a truth person who reached the 32-card window under the ungated "
            "vocabulary no longer does, because the enlarged pool pushed them out of it."
            if lost
            else "no case lost a truth person from the window."
        )
        + " A pool that contains the right person but no longer shows them to the "
        "re-rank is not a recall improvement the full system can use. *(Measured.)*",
        "",
        "**Recommendation: close the df-floor-3 form as a ranking lever; do not spend a "
        "paid arm on it.** (Reasoned.) The flag stays in the codebase and stays off. "
        "What would change this reading is a different *motivation*: the vocabulary "
        "size is also a prompt-size and term-review cost, and if that cost is ever the "
        "reason to gate, the price is now known and quotable rather than hypothetical — "
        f"{detail['hit_at_1_delta']:+.3f} Hit@1 on the deterministic arm. What this "
        "study did **not** measure, and nobody should assume from it, is a lower floor "
        "(df 2), a specialization-only floor, or the same floor with a wider re-rank "
        "window; those are untested, not rejected. *(Reasoned.)*",
    ]


def g6_recommendation() -> list[str]:
    gate = sweeps.gate_g6()
    detail = gate.detail
    base, variant = _arm(sweeps.BASE_CONDITION), _arm(sweeps.G6_CONDITION)
    control = _arm(sweeps.G6_CONTROL)
    scale = float(detail["mean_strength_scale"])
    return [
        *_guard(gate),
        "",
        "### G6 — strength-weighted specialization match: **close**",
        "",
        "**Read against the current weights, G6 looks like a small win.** Hit@1 "
        f"{base.hit_at_1:.3f} → {variant.hit_at_1:.3f} "
        f"({detail['vs_base_hit_at_1']:+.3f}), MRR {base.mrr:.3f} → {variant.mrr:.3f} "
        f"({detail['vs_base_mrr']:+.3f}). Wave 1 predicted exactly this reading and "
        "warned against it. *(Measured.)*",
        "",
        "**Read against its control, the label adds nothing — and on these cases takes "
        "a little back.** Giving *every* candidate the same "
        f"average credit ({scale:.4f}, measured from G6's own output across "
        f"{len(sweeps.strength_scales(sweeps.load_condition(sweeps.BASE_CONDITION), sweeps.load_condition(sweeps.G6_CONDITION)))} "
        f"credited matches) scores Hit@1 {control.hit_at_1:.3f} and MRR "
        f"{control.mrr:.3f} — **better than the person-varying arm on both** "
        f"({detail['vs_control_hit_at_1']:+.3f} Hit@1 and "
        f"{detail['vs_control_mrr']:+.3f} MRR for G6 against the control). At this "
        f"sample size {_cases(detail['vs_control_hit_at_1'])} of Hit@1 is not a "
        "measurement of how much worse the label is; what it is, is the absence of any "
        "evidence that the label helps — the arm that was supposed to earn its keep by "
        "separating specialists from dabblers cannot beat a constant. *(Measured; the "
        "reading is reasoned.)*",
        "",
        "**This is wave 1's finding, confirmed and sharpened on a better instrument.** "
        "Wave 1 measured the control reproducing the person-varying arm *almost* exactly "
        "(MRR 0.293 against 0.296) on 30 v1-manifest cases with a person-level stand-in "
        "for the primary share. Here the share is the engine's own per-edge "
        "`primary_evidence_count`, the pools are pinned, and the two arms separate: the "
        "control is ahead. *(Measured.)*",
        "",
        f"**The window half of the gate passes, and it is weaker than it looks.** "
        f"{detail['cases_with_changed_window']} of {len(sweeps.load_condition(sweeps.BASE_CONDITION))} "
        "cases show a changed window population, so G6 genuinely moves who the re-rank "
        "would be shown — but "
        + (
            "**no truth person enters or leaves the window on any case**. It reshuffles "
            "which non-truth candidates fill the 32 cards. That is movement a paid arm "
            "could in principle exploit (the re-rank might rank the survivors "
            "differently), but it is not the mechanism the lever was proposed on, which "
            "was surfacing specialists the flat match was burying."
            if not detail.get("truth_people_moved_in_or_out_of_window")
            else f"{detail['truth_people_moved_in_or_out_of_window']} truth-person "
            "slot(s) move with it."
        )
        + " *(Measured; the reading is reasoned.)*",
        "",
        "**Recommendation: close G6.** (Reasoned.) The mechanism is the one the PRD asks "
        "for and the label has genuine spread in the data — 58% of specialization edges "
        "were never once called primary — but two independent measurements now agree "
        "that the spread does not separate the right people. The flag stays off and the "
        "backlog item can be closed rather than carried.",
        "",
        "**One thing worth carrying forward, and it is not G6.** The *control* is the arm "
        f"that improved: scaling `specialization_match` by a constant {scale:.4f} for "
        f"everyone moves the deterministic arm Hit@1 {base.hit_at_1:.3f} → "
        f"{control.hit_at_1:.3f} and MRR {base.mrr:.3f} → {control.mrr:.3f} "
        f"({detail['control_vs_base_mrr']:+.3f}). That is close to — though not exactly "
        "— lowering the component's weight, since `combine_parts` renormalizes over the "
        "components present and scaling a *value* and scaling a *weight* have different "
        "denominators. Benchmark v2's sweep already moved this weight from 0.40 to 0.25 "
        "for the same reason, and this says it may want to go lower still. **That is a "
        "weight question for the freeze order to sweep properly on the full system, not "
        "a result to adopt from this table** — it is 2 cases of Hit@1 on 28, offline, "
        "and the deterministic arm is not the shipped ranking. *(The numbers are "
        "measured; treating them as a weight lead rather than a weight decision is "
        "reasoned.)*",
    ]


def summary_table(gates: Sequence[sweeps.Gate]) -> list[str]:
    verdicts = {"G3a": "**close**", "G6": "**close**"}
    return [
        "| Lever | Tier-1 gate | Paid arm | Recommendation |",
        "|---|---|---|---|",
        *(
            f"| {gate.lever} | {'pass' if gate.passed else 'stop'} | "
            f"{'run' if gate.passed else 'not run'} | {verdicts.get(gate.lever, '—')} |"
            for gate in gates
        ),
    ]


def recommendations_section() -> list[str]:
    gates = sweeps.gates()
    return [
        "",
        "## Recommendation per lever",
        "",
        *summary_table(gates),
        "",
        f"Both recommendations are stated against {_floor_line()}, measured in work item "
        "1 of this study — not against the 0.100 borrowed from the v1 instrument.",
        *g3a_recommendation(),
        *g6_recommendation(),
        "",
        "### What this leaves for the freeze order",
        "",
        "Neither lever should be bundled into the config freeze. (Reasoned.) The wave-1 "
        "deterministic-side shortlist is now empty: G5 was closed on the corpus having "
        "no confidence spread, G3a and G6 are closed here on measurement, and G7 was "
        "corrected by the rerank-redesign study. The one live lead this study produced "
        "is a *weight*, not a flag — see the last paragraph of G6 — and it belongs to a "
        "sweep, not to an adoption.",
    ]


__all__ = [
    "WRITTEN_FOR",
    "g3a_recommendation",
    "g6_recommendation",
    "recommendations_section",
    "summary_table",
]
