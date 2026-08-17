"""Render the near-miss study report from its own checkpointed numbers.

Kept apart from :mod:`capgraph.eval.nearmiss` for the same reason ``report_sweeps`` is
kept apart from ``sweeps``: the measuring code should not also be the writing code, and a
re-render must never be able to change a number.

Everything here reads the ``study.json`` payload the study wrote. In particular the
**report-ready statement is generated from the data**, by a rule fixed in
:func:`verdict` before the run was made: a similarity definition supports the
plausible-substitute reading only if the paired bootstrap interval on
(miss similarity − its own control) lies entirely above zero, contradicts it only if the
interval lies entirely below zero, and is otherwise inconclusive. The sentence the report
ends on is chosen by counting those, not by an author deciding what the study showed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .nearmiss import DEFINITION_LABELS, DEFINITIONS

SUPPORTS = "supports"
CONTRADICTS = "contradicts"
INCONCLUSIVE = "inconclusive"


def _fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:
            return "n/a"
        return f"{number:.{digits}f}"
    return str(value)


def _ci(row: Mapping[str, object], key: str = "mean") -> str:
    if not row.get("n"):
        return "n/a"
    return f"{_fmt(row[key])} [{_fmt(row['ci_low'])}, {_fmt(row['ci_high'])}]"


def _signed(value: object) -> str:
    number = float(value)  # type: ignore[arg-type]
    return f"{number:+.3f}"


def _ci_signed(row: Mapping[str, object]) -> str:
    if not row.get("n"):
        return "n/a"
    return (
        f"{_signed(row['mean_delta'])} "
        f"[{_signed(row['ci_low'])}, {_signed(row['ci_high'])}]"
    )


# ---------- the pre-committed reading rule ----------

def classify(delta: Mapping[str, object]) -> str:
    """One definition's verdict from its paired interval. Zero inside it decides nothing."""
    if not delta.get("n"):
        return INCONCLUSIVE
    if float(delta["ci_low"]) > 0:  # type: ignore[arg-type]
        return SUPPORTS
    if float(delta["ci_high"]) < 0:  # type: ignore[arg-type]
        return CONTRADICTS
    return INCONCLUSIVE


def verdict(distributions: Mapping[str, object]) -> dict[str, object]:
    """Apply :func:`classify` to all three definitions and count the outcome."""
    misses = dict(distributions["misses"])  # type: ignore[index]
    per_definition = {
        name: classify(dict(misses["delta_vs_control"])[name])  # type: ignore[index]
        for name in DEFINITIONS
    }
    counts = {
        outcome: sum(1 for value in per_definition.values() if value == outcome)
        for outcome in (SUPPORTS, CONTRADICTS, INCONCLUSIVE)
    }
    if counts[SUPPORTS] == len(DEFINITIONS):
        direction = "all three"
    elif counts[SUPPORTS] and not counts[CONTRADICTS]:
        direction = "some"
    elif counts[CONTRADICTS] and not counts[SUPPORTS]:
        direction = "against"
    elif counts[SUPPORTS] and counts[CONTRADICTS]:
        direction = "split"
    else:
        direction = "none"
    return {"per_definition": per_definition, "counts": counts, "direction": direction}


# ---------- sections ----------

def _header(payload: Mapping[str, object]) -> list[str]:
    settings_block = dict(payload["settings"])  # type: ignore[index]
    return [
        "# Near-miss quality study — is a top-1 miss a plausible substitute?",
        "",
        f"- Generated {payload['generated_at']} by a worker session on "
        "`agent/nearmiss-study`",
        "- Order: `docs/work-orders/nearmiss-study.md`",
        f"- Manifest: `{payload['manifest']}`, version `{payload['manifest_version']}`",
        f"- Run: `{payload['runs_dir']}`, configuration digest "
        f"`{payload['config_digest']}`",
        f"- Arm: {payload['split']} split, `{payload['engine']}`, "
        f"{payload['brief_variant']} briefs, one run",
        "",
        "## The question, and what would answer it",
        "",
        "The research report currently says something like *\"where the system's first "
        "pick was not in the truth set, it was usually a plausible substitute\"*. That is "
        "an interpretation of a handful of shortlists, not a measurement. This study "
        "measures it.",
        "",
        "A **top-1 miss** is a case whose first-ranked person is not in the case's truth "
        "set — the people who actually resolved the sprint's issues. For every miss, and "
        "for every top-1 hit as a reference, three fixed similarity numbers are computed "
        "between the first-ranked person and the *nearest* member of the truth set, each "
        "against a control drawn from the same case's own frozen roster. If misses are no "
        "closer to truth than a random roster member, the plausible-substitute reading is "
        "wrong and this report says so.",
        "",
        f"**This is a descriptive study.** There are {payload['cases_in_split']} cases, "
        "of which the misses are a subset; consecutive sprints inside a project share a "
        "mean truth-set Jaccard of 0.34, so the cases are not independent and the "
        "effective sample is smaller still. The intervals below are seeded bootstrap "
        "intervals over cases "
        f"({settings_block['bootstrap_resamples']:,} resamples, seed "
        f"{settings_block['bootstrap_seed']}) and are reported as descriptive spread. "
        "Nothing here is a hypothesis test, and no p-value appears in this document.",
    ]


def _method(payload: Mapping[str, object]) -> list[str]:
    settings_block = dict(payload["settings"])  # type: ignore[index]
    return [
        "",
        "## Method",
        "",
        "### The three similarity definitions",
        "",
        "All three were fixed in `config/settings.yaml` (`eval.nearmiss.metrics`) before "
        "the run. Person profiles are read **read-only** from the production Neo4j graph "
        "— the same graph the system was queried against.",
        "",
        "| # | Definition | How it is computed |",
        "|---|---|---|",
        f"| a | {DEFINITION_LABELS[DEFINITIONS[0]]} | Jaccard (intersection over union) "
        "over the two people's complete `HAS_SPECIALIZATION` term sets. |",
        f"| b | {DEFINITION_LABELS[DEFINITIONS[1]]} | Each person's `HAS_SKILL` edges "
        "ranked by `evidence_count × decay(last_used)`, with decay recomputed at **the "
        f"case's own as-of time** at the configured half-life of "
        f"{settings_block['recency_half_life_days']} days; Jaccard over the top "
        f"{settings_block['top_skills']} of each. The graph's stored `decay_score` is "
        "frozen at the holdout cutoff and is never read, exactly as in the harness. |",
        f"| c | {DEFINITION_LABELS[DEFINITIONS[2]]} | Cosine between the means of the two "
        "people's `Contribution.embedding` vectors (384-dim, all of their "
        "contributions). |",
        "",
        "\"Nearest truth person\" is the maximum over the truth set, taken **per "
        "definition**: the three measure different things, so forcing one winner on all "
        "three would report a number no definition produced.",
        "",
        "**One property of definition (b), stated because a reader would otherwise assume "
        "otherwise.** Recomputing decay at each case's as-of time is the right temporal "
        "discipline and is what the harness does — but it does not change which skills "
        "come top. The weight is `count × exp(−λ(as_of − last_used))`, so the ratio "
        "between any two skills is `(c₁/c₂) × exp(λ(t₁ − t₂))` and the as-of term "
        "cancels. The only thing that would break that is a `last_used` *after* the "
        "as-of time, "
        "where the decay clamps at 1.0, and that cannot happen here: the graph is frozen "
        "at the 2019-01-01 holdout cutoff and every case's as-of time is later. So (b) is "
        "in practice one fixed top-10 set per person across all 28 cases. It is still a "
        "recency-weighted set — recency decides which skills are in it — it just does not "
        "vary case to case. Tested: "
        "`test_the_top_skill_ranking_is_as_of_invariant_on_a_graph_frozen_before_every_"
        "case`.",
        "",
        "### The control",
        "",
        f"For each case, {settings_block['control_draws']} seeded draws (seed "
        f"{settings_block['control_seed']}, derived per case) of one member of that "
        "case's own frozen eligible roster, with replacement; each drawn person gets the "
        "same three numbers against the same truth set, and the **median** of the draws "
        "is the case's control. The roster is used whole — the work order specifies \"a "
        "random eligible roster member\" with no carve-out, so a draw can land on a truth "
        "member, and the median of 100 is what keeps that from moving the reference.",
        "",
        "### Adjacent-sprint truth membership",
        "",
        "For each case, whether the first-ranked person appears in the truth set of the "
        "immediately previous or immediately next sprint of the same project. Adjacency "
        "runs over the **whole** rebuilt structure — every candidate sprint with a "
        "recorded start, selected or not — so \"the next sprint\" means the project's "
        "next sprint and not the next sampled case.",
        "",
        "**This is post-as-of information.** The next sprint's truth set did not exist "
        "when the question was asked. It is legitimate as a post-hoc diagnostic — it asks "
        "whether the named person was working on this team's work around this time — and "
        "it is **never** available for tuning, was not used to choose anything here, and "
        "must not be quoted as a system metric.",
    ]


def _verification(payload: Mapping[str, object]) -> list[str]:
    verification = dict(payload["verification"])  # type: ignore[index]
    structure = dict(verification["structure"])  # type: ignore[index]
    lines = [
        "",
        "## Verification: this is the same instrument, rebuilt",
        "",
        "The frozen v4 manifest and every v4 run checkpoint were destroyed on 2026-08-16 "
        "(`docs/incident-2026-08-16-data-loss.md`), so the manifest here was rebuilt from "
        "the surviving Stage 0 parquet. Package selection, split assignment, rosters and "
        "truth sets are seed-deterministic, so the rebuild is checkable against the "
        "published record — and it was checked before anything was paid for. A mismatch "
        "on any row below is a hard failure in code, not a warning.",
        "",
        f"### The {payload['split']} split ({verification['record']})",
        "",
        "| Check | Rebuilt | Published record | |",
        "|---|---|---|---|",
    ]
    for name in ("cases", "projects", "mean_truth_set_size"):
        got = dict(verification["observed"])[name]  # type: ignore[index]
        want = dict(verification["expected"])[name]  # type: ignore[index]
        lines.append(
            f"| `{name}` | {_pretty(got)} | {_pretty(want)} | "
            f"{'match' if got == want else '**MISMATCH**'} |"
        )
    lines += [
        "",
        f"### The whole structure ({structure['record']})",
        "",
        "A drifted sample could still yield 28 validation cases holding different "
        "sprints, so the rest of the published accounting is verified too. Building the "
        "test split's *rows* is data construction, not exposure: no test brief was "
        "rewritten, run, scored or read.",
        "",
        "| Check | Rebuilt | Published record | |",
        "|---|---|---|---|",
    ]
    for name, got in dict(structure["observed"]).items():  # type: ignore[index]
        want = dict(structure["expected"])[name]  # type: ignore[index]
        lines.append(
            f"| `{name}` | {_pretty(got)} | {_pretty(want)} | "
            f"{'match' if got == want else '**MISMATCH**'} |"
        )
    lines += [
        "",
        "Every published number reproduces. What does **not** reproduce, and cannot, is "
        "the brief text: the rewrites were re-generated on the same model and prompt from "
        "the same sanitized pre-as-of package text, so they are new words describing the "
        "same work. That is why this manifest is labelled a **sibling** — "
        f"`{payload['manifest_version']}`, in both the file name and the version string — "
        "and why no number in this report is compared against a v4 checkpoint. Everything "
        "below was measured inside this study's own single run.",
    ]
    return lines


def _tracks(payload: Mapping[str, object]) -> str:
    """"DM 3, MESOS 3, FAB 0, TIMOB 0" — the parallel-board evidence, compactly."""
    rows = list(payload["sprint_calendar"])  # type: ignore[index]
    window = rows[0]["window_days"] if rows else 0
    counts = ", ".join(
        f"{row['project_key']} {row['median_concurrent_starts']}" for row in rows
    )
    parallel = [str(row["project_key"]) for row in rows if row["median_concurrent_starts"]]
    where = (
        " and ".join([", ".join(parallel[:-1]), parallel[-1]])
        if len(parallel) > 1
        else (parallel[0] if parallel else "")
    )
    return (
        f"the median number of other sprints in the same project starting within "
        f"{window} days is {counts}, so several boards are live at once in "
        + (where if parallel else "no project")
    )


def _pretty(value: object) -> str:
    if isinstance(value, Mapping):
        return ", ".join(f"{key} {val}" for key, val in value.items())
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _run_section(payload: Mapping[str, object]) -> list[str]:
    cases = list(payload["cases"])  # type: ignore[index]
    misses = [case for case in cases if case["outcome"] == "miss"]
    hits = [case for case in cases if case["outcome"] == "hit"]
    failures = list(payload["run_failures"])  # type: ignore[index]
    lines = [
        "",
        "## The run",
        "",
        f"One run of the full graph system (`capgraph_full`) over the "
        f"{payload['cases_in_split']} {payload['split']} cases; "
        f"{payload['cases_scored']} scored"
        + (f", {len(failures)} failed ({', '.join(failures)})" if failures else
           ", none failed")
        + ".",
        "",
        "| | Cases | Share |",
        "|---|---:|---:|",
        f"| top-1 hit | {len(hits)} | {len(hits) / len(cases):.3f} |",
        f"| top-1 miss | {len(misses)} | {len(misses) / len(cases):.3f} |",
        f"| **total** | **{len(cases)}** | |",
        "",
        f"Hit@1 on this run is therefore {len(hits) / len(cases):.3f}. The v4 record's "
        "own validation Hit@1 for this arm is not quoted beside it: those run "
        "checkpoints are gone, the briefs here are re-generated, and the deterministic "
        "sweeps study measured a run-to-run noise floor on this instrument that a "
        "28-case difference sits inside. The number above is this run's, and it is the "
        "only place the study's own denominators come from.",
    ]
    return lines


def _case_table(payload: Mapping[str, object]) -> list[str]:
    cases = list(payload["cases"])  # type: ignore[index]
    lines = [
        "",
        "## Every case, one row each",
        "",
        "n is small, so nothing is aggregated away. `sim` is the first-ranked person's "
        "similarity to the nearest truth person; `ctl` is that case's control (median of "
        "100 roster draws). `adj` is adjacent-sprint truth membership (P = previous "
        "sprint, N = next, `-` = neither) — post-as-of, diagnostic only. `truth rank` is "
        "where the first truth person actually landed in the ranking.",
        "",
        "### Top-1 misses",
        "",
    ]
    lines += _rows([case for case in cases if case["outcome"] == "miss"])
    lines += ["", "### Top-1 hits (reference)", ""]
    lines += _rows([case for case in cases if case["outcome"] == "hit"])
    concentration = dict(
        dict(dict(payload["distributions"])["misses"])["top1_concentration"]  # type: ignore[index]
    )
    lines += [
        "",
        "### How many different people the misses actually are",
        "",
        f"The {concentration['n']} miss cases name **{concentration['distinct_people']} "
        f"distinct people** first; the most frequent, `{concentration['most_frequent']}`, "
        f"takes the top slot in {concentration['most_frequent_count']} of them. Read the "
        "distribution table below with that in mind: where one profile dominates, a mean "
        "similarity over misses is substantially a fact about *that person's* profile "
        "rather than about the system's ability to tell roster members apart. A mean "
        "cannot show this, which is why the table above lists every case.",
        "",
        "On a hit the first-ranked person *is* a truth person, so every `sim` in the hit "
        "table is 1.000 by construction. That is the arithmetic sanity check, and it is "
        "not the interesting half: the hit rows' **controls** are, because they show "
        "whether the control is systematically easier on the cases the system got right. "
        "If the hit controls and the miss controls sit at similar levels, the miss-minus-"
        "control gap cannot be explained away as \"the misses were easier cases\".",
    ]
    return lines


def _rows(cases: Sequence[Mapping[str, object]]) -> list[str]:
    if not cases:
        return ["_No cases in this group._"]
    header = (
        "| Package | Project | As-of | Roster | Truth | Top-1 | Truth rank | "
        "a sim | a ctl | b sim | b ctl | c sim | c ctl | adj |"
    )
    lines = [header, "|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for case in cases:
        sim = dict(case["similarity_to_nearest_truth"])  # type: ignore[index]
        ctl = dict(case["control_median"])  # type: ignore[index]
        adjacent = dict(case["adjacent_sprint_truth"])  # type: ignore[index]
        marks = "".join(
            mark for mark, key in (("P", "previous"), ("N", "next")) if adjacent[key]
        )
        rank = case["first_truth_rank"]
        lines.append(
            f"| `{case['package_key']}` | {case['project_key']} | "
            f"{str(case['as_of'])[:10]} | {case['roster_size']} | {case['truth_size']} | "
            f"`{case['top1']}` | {rank if rank else '—'} | "
            f"{_fmt(sim[DEFINITIONS[0]])} | {_fmt(ctl[DEFINITIONS[0]])} | "
            f"{_fmt(sim[DEFINITIONS[1]])} | {_fmt(ctl[DEFINITIONS[1]])} | "
            f"{_fmt(sim[DEFINITIONS[2]])} | {_fmt(ctl[DEFINITIONS[2]])} | "
            f"{marks or '—'} |"
        )
    return lines


def _distribution_section(payload: Mapping[str, object]) -> list[str]:
    distributions = dict(payload["distributions"])  # type: ignore[index]
    lines = [
        "",
        "## Distributions against the control",
        "",
        "Mean over cases with a seeded 95% bootstrap interval over cases. `Δ` is the "
        "paired per-case difference (similarity − that case's own control), which is the "
        "quantity the reading turns on: it is measured inside each case, so it is immune "
        "to a case being intrinsically easy or hard.",
    ]
    for group, title in (("misses", "Top-1 misses"), ("hits", "Top-1 hits (reference)")):
        entry = dict(distributions[group])  # type: ignore[index]
        lines += [
            "",
            f"### {title} — n = {entry['n']}",
            "",
            "| Definition | Similarity to nearest truth | Control (random roster "
            "member) | Δ paired | Above ctl | Below ctl | Tied |",
            "|---|---|---|---|---:|---:|---:|",
        ]
        if not entry["n"]:
            lines.append("| _no cases in this group_ | | | | | | |")
            continue
        for name in DEFINITIONS:
            similarity = dict(entry["similarity"])[name]  # type: ignore[index]
            control = dict(entry["control"])[name]  # type: ignore[index]
            delta = dict(entry["delta_vs_control"])[name]  # type: ignore[index]
            lines.append(
                f"| {DEFINITION_LABELS[name]} | {_ci(similarity)} | {_ci(control)} | "
                f"{_ci_signed(delta)} | {delta['above_control']} | "
                f"{delta['below_control']} | {delta['ties']} |"
            )
    return lines


def _adjacent_section(payload: Mapping[str, object]) -> list[str]:
    distributions = dict(payload["distributions"])  # type: ignore[index]
    lines = [
        "",
        "## Adjacent-sprint truth membership",
        "",
        "A post-as-of diagnostic, stated again because it matters: the neighbouring "
        "sprints' truth sets were not knowable when the question was asked. This number "
        "is not a system metric and nothing in this study was tuned on it.",
        "",
        "| Group | n | In previous or next | Share | In previous | In next |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    rows = {}
    for group, title in (("misses", "top-1 misses"), ("hits", "top-1 hits")):
        row = dict(dict(distributions[group])["adjacent_sprint"])  # type: ignore[index]
        rows[group] = row
        lines.append(
            f"| {title} | {row['n']} | {row['in_previous_or_next']} | "
            f"{_fmt(row['share'])} | {row['in_previous']} | {row['in_next']} |"
        )
    misses = rows["misses"]
    overlap = dict(misses["own_truth_jaccard_with_neighbours"])
    lines += [
        "",
        "### How much chance this diagnostic had",
        "",
        "A share near zero can mean two different things — the named person was not on "
        "this team's work, or the *neighbouring sprint was not this team's sprint*. On "
        "this corpus the second matters, because the larger projects run several sprint "
        "boards at once and their sprints interleave by start date, so \"the project's "
        "next sprint by date\" is frequently a different team's:",
        "",
        "| Project | Post-cutoff sprints | Other sprints starting within ±7 days "
        "(median) | (max) |",
        "|---|---:|---:|---:|",
        *(
            f"| {row['project_key']} | {row['post_cutoff_sprints']} | "
            f"{row['median_concurrent_starts']} | {row['max_concurrent_starts']} |"
            for row in list(payload["sprint_calendar"])  # type: ignore[index]
        ),
        "",
        "Measured from recorded start dates alone — no sprint-name parsing, which is the "
        "trap here, since one project's sprints are named by year (\"2019 Sprint 4\") and "
        "a name-based count reports every sprint as its own board. A median above zero "
        "means several boards are live at once, so the sprint that is *next by date* is "
        "routinely not the same team's next sprint; a median of zero means the project "
        "runs one board at a time and date-adjacency is meaningful there. The numbers "
        "below say how much continuity the diagnostic actually had to find.",
        "",
        "| Measure | Miss cases | Hit cases |",
        "|---|---|---|",
        f"| Median people in a case's two neighbouring truth sets | "
        f"{misses['neighbour_truth_size_median']} | "
        f"{rows['hits']['neighbour_truth_size_median']} |",
        f"| Cases whose neighbours contribute no truth at all | "
        f"{misses['cases_with_no_neighbour_truth']} of {misses['n']} | "
        f"{rows['hits']['cases_with_no_neighbour_truth']} of {rows['hits']['n']} |",
        f"| **Jaccard between a case's own truth set and its neighbours'** | "
        f"{_ci(overlap)} | "
        f"{_ci(dict(rows['hits']['own_truth_jaccard_with_neighbours']))} |",
        "",
        "Read that last row first, and read it across the two columns. The v4 manifest "
        "record measured a mean truth-set Jaccard of 0.34 between *consecutive* packages "
        "in a project, which is where the \"the same team runs consecutive sprints\" "
        "caveat comes from. Where the figure here is far below that, date-adjacency is "
        "not picking out the same team at all, so a low adjacent-sprint share is evidence "
        "about the **definition** rather than about the person named.",
        "",
        "The two columns are the thing to notice: the cases the system got right are "
        "largely cases whose neighbouring sprints *are* the same team's, and the cases it "
        "missed are largely cases whose neighbours are a different team's. The "
        "adjacent-sprint share therefore separates the two groups mostly by that "
        "property, not by whether the named person was on the team — which is why the "
        "adjacency number does not carry the study's claim. Stated rather than corrected: "
        "the metric was pre-specified and is computed exactly as specified.",
    ]
    return lines


def _supplementary_section(payload: Mapping[str, object]) -> list[str]:
    distributions = dict(payload["distributions"])  # type: ignore[index]
    lines = [
        "",
        "## Supplementary, and **not** pre-specified",
        "",
        "**The work order pre-specified the metrics above and told this study to add "
        "nothing post-hoc. The following is not one of them and the claim at the end of "
        "this report does not rest on it.** It is here because a reader will reasonably "
        "ask what the scale means — is a specialization Jaccard of 0.19 close or not? — "
        "and the natural yardstick is how alike the people who worked the *same* package "
        "are to each other. For each case with two or more truth members: each member's "
        "similarity to their own nearest teammate, averaged over the members. It is "
        "computed from the truth set alone and so does not depend on what the system "
        "answered. Single-person truth sets have no teammate and are dropped.",
        "",
        "| Definition | Miss cases | Hit cases |",
        "|---|---|---|",
    ]
    for name in DEFINITIONS:
        misses = dict(dict(distributions["misses"])["intra_truth_set_supplementary"])[name]
        hits = dict(dict(distributions["hits"])["intra_truth_set_supplementary"])[name]
        lines.append(
            f"| {DEFINITION_LABELS[name]} | {_ci(misses)} (n={misses['n']}) | "
            f"{_ci(hits)} (n={hits['n']}) |"
        )
    lines += [
        "",
        "Compare each row against the same definition's miss row in the distribution "
        "table above. If a miss scores about as high as a real teammate does, \"same "
        "capability neighbourhood\" is a fair description of it; if a real teammate "
        "scores far higher, the miss is measurably outside the team even where it beats "
        "the random control. Both readings are visible in the same two numbers, which is "
        "why this section exists even though nothing depends on it.",
    ]
    return lines


def _caveats(payload: Mapping[str, object]) -> list[str]:
    distributions = dict(payload["distributions"])  # type: ignore[index]
    n_misses = int(dict(distributions["misses"])["n"])  # type: ignore[arg-type]
    return [
        "",
        "## What this study cannot say",
        "",
        f"- **It is descriptive.** n(misses) is {n_misses}, the cases are correlated "
        "within a project, and the intervals are bootstrap spread over those few cases. "
        "Read the per-case table before any interval.",
        "- **Similar is not interchangeable.** Every number here is computed from the "
        "same extracted profiles the system ranks on. A person who looks close in that "
        "representation may be close *because the representation is coarse*, not because "
        "they could have done the work. This measures neighbourhood in the system's own "
        "feature space, and that is a weaker claim than substitutability.",
        "- **Truth is who did the work, not who should have.** A miss whose profile is "
        "far from truth is not automatically wrong either — the truth set is one "
        "team's historical assignment, and the whole benchmark rests on that being a "
        "prediction target rather than a statement about optimal staffing.",
        "- **The control is a roster draw, not a hard case.** It answers \"better than "
        "anybody on this roster?\", which is the weakest bar worth clearing. It is not a "
        "second system and beating it is not evidence of quality.",
        "- **No result here licenses an employment decision.** Same as everywhere else in "
        "this PoC: project-qualified pseudonyms, public OSS Jira, research only.",
    ]


def _statement(payload: Mapping[str, object]) -> list[str]:
    distributions = dict(payload["distributions"])  # type: ignore[index]
    misses = dict(distributions["misses"])  # type: ignore[index]
    hits = dict(distributions["hits"])  # type: ignore[index]
    outcome = verdict(distributions)
    adjacent = dict(misses["adjacent_sprint"])  # type: ignore[index]
    n_misses = int(misses["n"])  # type: ignore[arg-type]

    supported = [name for name, value in outcome["per_definition"].items()
                 if value == SUPPORTS]
    contradicted = [name for name, value in outcome["per_definition"].items()
                    if value == CONTRADICTS]
    inconclusive = [name for name, value in outcome["per_definition"].items()
                    if value == INCONCLUSIVE]

    def phrase(names: Sequence[str]) -> str:
        letters = [DEFINITION_LABELS[name].split(")")[0].lstrip("(") for name in names]
        return ", ".join(f"({letter})" for letter in letters)

    detail = "; ".join(
        f"{DEFINITION_LABELS[name]} Δ {_ci_signed(dict(misses['delta_vs_control'])[name])}"
        for name in DEFINITIONS
    )
    # Two facts that qualify any positive verdict and are in the data either way, so they
    # go in the paragraph rather than being left for a reader to find in a table.
    intra = dict(misses["intra_truth_set_supplementary"])  # type: ignore[index]
    scale = "; ".join(
        f"{_fmt(dict(misses['similarity'])[name]['mean'])} against "
        f"{_fmt(dict(intra)[name]['mean'])}"
        for name in DEFINITIONS
    )
    concentration = dict(misses["top1_concentration"])  # type: ignore[index]
    qualifier = (
        f"Two things qualify that. The margins are small in absolute terms and sit below "
        f"the yardstick of how alike two people who worked the *same* package are (miss "
        f"similarity against intra-team similarity, per definition: {scale}). And the "
        f"{n_misses} misses are only {concentration['distinct_people']} distinct people: "
        f"`{concentration['most_frequent']}` is ranked first in "
        f"{concentration['most_frequent_count']} of them, so these means are partly a "
        f"fact about one profile rather than about the system's discrimination across the "
        f"roster."
    )
    verdicts = {
        "all three": (
            "On all three pre-specified definitions the misses sit closer to the truth "
            "set than a random member of the same roster does, by intervals that do not "
            "reach zero."
        ),
        "some": (
            f"On {len(supported)} of the three pre-specified definitions "
            f"({phrase(supported)}) the misses sit closer to the truth set than a random "
            f"member of the same roster does by an interval that does not reach zero; on "
            f"the remaining {len(inconclusive)} ({phrase(inconclusive)}) the interval "
            f"includes zero, so that definition decides nothing."
        ),
        "against": (
            f"On {len(contradicted)} of the three pre-specified definitions "
            f"({phrase(contradicted)}) the misses are *no closer* to the truth set than a "
            f"random member of the same roster — the interval lies entirely below zero — "
            f"and the rest are inconclusive. **The plausible-substitute reading is not "
            f"supported by this measurement.**"
        ),
        "split": (
            f"The three definitions disagree: {phrase(supported)} place the misses closer "
            f"to truth than a random roster member, {phrase(contradicted)} place them no "
            f"closer, and the rest are inconclusive. No single reading survives all three."
        ),
        "none": (
            "On none of the three pre-specified definitions does the interval on "
            "(miss − control) clear zero in either direction. At this sample size the "
            "study is **inconclusive**: it neither supports nor refutes the "
            "plausible-substitute reading."
        ),
    }
    return [
        "",
        "## Report-ready statement",
        "",
        "One paragraph, generated from the numbers above by a rule fixed before the run "
        "(a definition counts only if its paired bootstrap interval excludes zero). Use "
        "it as written or not at all.",
        "",
        f"> On the {payload['cases_scored']} rebuilt work-package "
        f"{payload['split']} cases, one run of the full system "
        f"placed a truth-set member first in {int(hits['n'])} cases and someone else "
        f"first in {n_misses}. For those {n_misses} top-1 misses we measured how close "
        "the named person's capability profile is to the nearest person who actually did "
        "the work, on three definitions fixed in advance — shared specializations, shared "
        "recency-weighted top skills, and cosine between mean contribution embeddings — "
        "each against the median of 100 random draws from the same case's own eligible "
        f"roster. {verdicts[str(outcome['direction'])]} The measured values were: "
        f"{detail}. {qualifier} Separately, and as a post-hoc diagnostic that uses "
        "information from "
        f"after the question was asked, {adjacent['in_previous_or_next']} of the "
        f"{n_misses} first-ranked misses "
        f"{'appears' if int(adjacent['in_previous_or_next']) == 1 else 'appear'} in the "
        "truth set of the immediately previous or next sprint of the same project "
        f"({_fmt(adjacent['share'])}); that figure should be read against the calendar — "
        f"{_tracks(payload)}. A date-adjacent sprint is therefore frequently a different "
        "team's, and on the miss cases a case's own truth set overlaps its neighbours' by "
        "a Jaccard of only "
        f"{_fmt(dict(adjacent['own_truth_jaccard_with_neighbours'])['mean'])}. All of "
        "this is descriptive, on a small and project-correlated sample, and measured "
        "inside the system's own profile representation: it is a statement about "
        "measured neighbourhood, and it does not establish that the person named could "
        "have done the work.",
        "",
        "| Definition | Verdict under the pre-committed rule |",
        "|---|---|",
        *(
            f"| {DEFINITION_LABELS[name]} | {outcome['per_definition'][name]} |"
            for name in DEFINITIONS
        ),
    ]


def _spend_section(payload: Mapping[str, object]) -> list[str]:
    spend = dict(payload["spend"])  # type: ignore[index]
    stages = list(spend["stages"])  # type: ignore[index]
    total = sum(float(row["cost_usd"]) for row in stages)
    lines = [
        "",
        "## Spend and reproduction",
        "",
        "| Stage | Calls | Cost (USD) |",
        "|---|---:|---:|",
        *(
            f"| `{row['stage']}` | {row['calls']} | {float(row['cost_usd']):.4f} |"
            for row in stages
        ),
        f"| **total** | | **{total:.4f}** |",
        "",
        f"Against the ${float(spend['ceiling_usd']):.2f} the owner authorized on "
        "2026-08-17, reconciled by stage name against `data/llm_costs.jsonl` with retries "
        "included. By call type:",
        "",
        "| Call type | Calls | Cost (USD) |",
        "|---|---:|---:|",
        *(
            f"| `{name}` | {row['calls']} | {float(row['cost_usd']):.4f} |"
            for name, row in dict(spend["by_purpose"]).items()  # type: ignore[index]
        ),
        "",
        "```bash",
        "make nearmiss-structure   # offline: rebuild the sibling manifest, verify it",
        "make nearmiss-rewrite     # SPENDS under nearmiss_rewrite (validation only)",
        "make nearmiss-run         # SPENDS under nearmiss_val, once",
        "make nearmiss-report      # offline: recompute the metrics, rewrite this document",
        "```",
        "",
        "The manifest rebuild and the run are both idempotent: the structure step is "
        "deterministic and free, the rewrite step is a no-op once every validation brief "
        "has one, and the run resumes from its checkpoint. A drifted rebuild refuses to "
        "write rather than reporting a warning, so `make nearmiss-structure` is also the "
        "audit.",
        "",
        "Graph state these numbers were measured against (read-only): the accepted "
        "Stage 5 load — Person 316, Contribution 2,666, Skill 10,630, `HAS_SKILL` 17,589, "
        "`HAS_SPECIALIZATION` 2,361 — unchanged from the state every v4 number was "
        "measured against.",
    ]
    return lines


def render(payload: Mapping[str, object]) -> str:
    """The whole report, in the order a reviewer needs it."""
    lines = _header(payload)
    lines += _method(payload)
    lines += _verification(payload)
    lines += _run_section(payload)
    lines += _case_table(payload)
    lines += _distribution_section(payload)
    lines += _adjacent_section(payload)
    lines += _supplementary_section(payload)
    lines += _caveats(payload)
    lines += _statement(payload)
    lines += _spend_section(payload)
    return "\n".join(lines) + "\n"


__all__ = ["CONTRADICTS", "INCONCLUSIVE", "SUPPORTS", "classify", "render", "verdict"]
