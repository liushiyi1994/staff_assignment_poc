"""Wave-1 offline measurements: every number `docs/improvement-wave1-report.md` claims.

    uv run python -m capgraph.eval.wave1 --all          # every measurement below

Five measurements, all offline and all free — no model call is made anywhere in this
module, and nothing here writes to a frozen benchmark namespace:

* ``--truncation`` (backlog G1) — how many descriptions the 1,200-character budget
  actually cuts, how much they lose, and how much of what is kept is pasted logs rather
  than prose. Reads MySQL, because the parquet export is already truncated and can only
  give a lower bound.
* ``--vocabulary`` (G3a) — document frequency per canonical term, and how many
  canonicals survive each candidate floor.
* ``--activity`` (G11a) — quarters since last contribution across the retained roster.
* ``--rescore`` (G5, G6, G11a) — the checkpointed validation score components re-scored
  with each flag's signal folded in, through the engine's own :func:`combine_parts`.
* ``--parity`` — the acceptance check: with every flag off, the three deterministic
  baselines reproduce the frozen v3 validation run exactly.

**What the re-score can and cannot say.** The v3 checkpoint stores the four aggregate
score components per candidate and nothing else, so a component the engine would compute
from *this role's* matched evidence cannot be reconstructed from it. The re-score
therefore joins a **person-level** stand-in — one confidence profile, one primary share
per person — onto each candidate. Where the engine would ask "how confident is the
evidence behind this match", the re-score asks "how confident is this person's evidence
in general". Both are reported with sensitivity bounds (best case and worst case per
person) so that a "no measurable effect" reading does not rest on the stand-in being
accurate. Only G11a is exact: activity currency is person-level in the engine too.

The other honest caveat is what the deltas here should be read against. The 0.100 floor
quoted throughout `docs/eval-results.md` is a *run-to-run* floor — it is what re-running
the pipeline moves by, with a fresh intent parse and a fresh retrieval pool. A re-score
re-uses one checkpoint, so it has no run-to-run variance at all; what it has is sampling
error on 30 cases, where a single case is 0.033 of Hit@1. Both are stated in the tables:
a delta below the sampling grain is nothing, and a delta below 0.100 would not survive
the pipeline actually being re-run.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .. import improvements
from ..models import Contribution
from ..pipeline.stage4_project import decay, period_end, snapshot_date
from ..query.rank import SCORE_COMPONENTS
from ..settings import DATA_DIR, settings
from .metrics import hit_at_k, load_manifest, mrr, query_context
from .paired import paired_binary, paired_bootstrap, render_paired
from .scores import CaseScores, RoleScores, config_path, evaluate_weights, retrieval_config

WAVE1_DIR = DATA_DIR / "wave1"
NORMALIZED_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
TERMS_PATH = DATA_DIR / "contributions" / "terms.jsonl"
V3_SCORES = DATA_DIR / "eval" / "v3" / "scores" / "validation.jsonl"

# The v2 section measured this by re-running one configuration unchanged; every v3
# lever was read against it. Quoted here so the tables carry their own yardstick.
RUN_TO_RUN_FLOOR = 0.100

HIT_KS = (1, 5, 10)


# ---------- G1: description truncation ----------

# TAWOS stores `Description_Text` with the original line breaks already replaced by runs
# of spaces — measured across the configured slice, not one row of it contains a newline
# character. Splitting on two or more consecutive spaces recovers the line structure well
# enough to judge a description line by line, which is the only way to tell a pasted log
# from a paragraph that happens to mention a version number.
SEGMENT_SPLIT = re.compile(r"\s{2,}")

# Prose-embedded noise. Fenced code is already excluded upstream (Stage 0 prefers
# `Description_Text` over `Description`), so what these catch is what the backlog says is
# left: pasted stack traces, log and console dumps, environment tables, and single-token
# boilerplate fields.
NOISE_LINE_PATTERNS = (
    re.compile(r"^\s*at\s+[\w$.]+\("),                                  # java stack frame
    re.compile(r"^\s*(Caused by|Traceback|Exception in thread)\b"),
    re.compile(r"^\s*[\w.$]+(Exception|Error):"),
    re.compile(r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}"),    # timestamped log
    re.compile(r"^\s*[IWEDF]\d{4}\s+\d{2}:\d{2}:\d{2}"),                # glog
    re.compile(r"^\s*\[?(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|FATAL)\]?[\s:]"),
    re.compile(r"^\s*File\s+\"[^\"]+\",\s+line\s+\d+"),                 # python traceback
    re.compile(r"^\s*[|+=-]{2,}"),                                      # table / rule row
    re.compile(r"^\s*[A-Za-z][\w .()\-/]{0,40}:\s+\S+\s*$"),            # key: single value
    re.compile(r"^\s*\$\s+\S"),                                         # shell transcript
    re.compile(r"^\s*[/~][\w./\-]{12,}\s*$"),                           # bare path
    re.compile(r"\|\s*\d+(\.\d+)?\s*[kKmMgG]?B\b"),                     # download table
    re.compile(r"\b\d{2}:\d{2}:\d{2}\b.*\b\d{2}:\d{2}:\d{2}\b"),        # console timings
)

# Second test, for machine output no pattern anticipates: English prose runs about 90%
# letters and spaces, while console output, hex, config blobs and tables are dense with
# digits, punctuation and symbols. Calibrated on a sample of the configured slice — see
# `docs/improvement-wave1-report.md`. Only applied to segments long enough for the ratio
# to mean anything.
MIN_PROSE_LETTER_SHARE = 0.75
MIN_SEGMENT_CHARS_FOR_RATIO = 40


def is_noise_line(line: str) -> bool:
    """Whether one recovered line reads as machine output rather than prose."""
    stripped = line.strip()
    if not stripped:
        return False
    if any(pattern.search(stripped) for pattern in NOISE_LINE_PATTERNS):
        return True
    if len(stripped) < MIN_SEGMENT_CHARS_FOR_RATIO:
        return False
    letters = sum(1 for char in stripped if char.isalpha() or char.isspace())
    return letters / len(stripped) < MIN_PROSE_LETTER_SHARE


def segments(text_: str) -> list[str]:
    """The description's recovered lines: non-blank runs between space runs."""
    return [part for part in SEGMENT_SPLIT.split(str(text_)) if part.strip()]


def noise_char_share(text_: str) -> float:
    """Share of a description's non-blank characters sitting on machine-output lines."""
    lines = segments(text_)
    total = sum(len(line.strip()) for line in lines)
    if total == 0:
        return 0.0
    return sum(len(line.strip()) for line in lines if is_noise_line(line)) / total


# Jira's fenced-block macros. The backlog records that fenced code is already excluded
# because Stage 0 prefers TAWOS's `Description_Text` over the raw `Description`. That
# holds for the *separate* `Description_Code` column, but `Description_Text` still
# carries inline {code}/{noformat} blocks, and `strip_markup` removes the delimiters
# while keeping the body — so this measures what is actually left.
CODE_FENCE = re.compile(r"\{(?:code|noformat)(?::[^}]*)?\}")


def code_char_share(text_: str) -> float:
    """Share of a description's characters sitting inside a {code}/{noformat} block."""
    body = str(text_)
    if not body:
        return 0.0
    fences = [match for match in CODE_FENCE.finditer(body)]
    if len(fences) < 2:
        return 0.0
    inside = 0
    for opening, closing in zip(fences[0::2], fences[1::2], strict=False):
        inside += max(0, closing.start() - opening.end())
    return min(1.0, inside / len(body))


@dataclass(frozen=True)
class TruncationMeasurement:
    """What the 1,200-character budget does to the corpus it is applied to."""

    source: str
    n_descriptions: int
    budget: int
    over_budget: int
    over_budget_share: float
    length_percentiles: dict[str, int]
    chars_kept_share_of_over_budget: float
    mid_word_cuts: int
    mid_word_cuts_remaining: int
    sentence_boundary_cuts: int
    word_boundary_cuts: int
    unchanged_cuts: int
    chars_lost_to_boundary_median: int
    majority_noise: int
    majority_noise_share: float
    majority_noise_over_budget: int
    mean_noise_char_share: float
    with_code_block: int
    with_code_block_share: float
    mean_code_char_share: float
    majority_non_prose: int
    majority_non_prose_share: float
    majority_non_prose_over_budget: int


def _percentiles(values: Sequence[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {}

    def at(q: float) -> int:
        return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]

    return {
        "min": ordered[0], "p50": at(0.50), "p75": at(0.75), "p90": at(0.90),
        "p95": at(0.95), "p99": at(0.99), "max": ordered[-1],
    }


def measure_truncation(*, chunk_size: int = 5000) -> TruncationMeasurement:
    """Measure description length and noise on the *untruncated* source text.

    Reads MySQL directly: `data/parquet/tickets.parquet` already holds the truncated
    strings, so it can only report how many landed exactly on the budget. Applies the
    same markup stripping Stage 0 applies, with the budget lifted, and then measures
    what the budget would have done — including which of the three cuts the new
    sentence-aware rule would take.
    """
    import pandas as pd
    from sqlalchemy import bindparam, text

    from ..pipeline.stage0_load import get_engine, strip_markup, truncate_at_boundary

    budget = int(settings["bucketing.max_description_chars"])
    unlimited = 10**9
    statement = text(
        """
        SELECT i.`Description_Text` AS description_text, i.`Description` AS description_raw
        FROM `Issue` AS i
        JOIN `Project` AS p ON p.`ID` = i.`Project_ID`
        WHERE p.`Project_Key` IN :project_keys
        """
    ).bindparams(bindparam("project_keys", expanding=True))

    lengths: list[int] = []
    over: list[int] = []
    kept: list[int] = []
    lost: list[int] = []
    cuts = Counter()
    noise_shares: list[float] = []
    code_shares: list[float] = []
    majority_noise = 0
    majority_noise_over = 0
    with_code = 0
    majority_non_prose = 0
    majority_non_prose_over = 0

    engine = get_engine()
    frames = pd.read_sql(
        statement,
        engine,
        params={"project_keys": list(settings["dataset.projects"])},
        chunksize=chunk_size,
    )
    for frame in frames:
        for row in frame.itertuples(index=False):
            raw = row.description_text if pd.notna(row.description_text) else None
            if raw is None or not str(raw).strip():
                raw = row.description_raw if pd.notna(row.description_raw) else None
            if raw is None or not str(raw).strip():
                continue
            cleaned = strip_markup(str(raw), unlimited)
            if not cleaned:
                continue
            lengths.append(len(cleaned))
            share = noise_char_share(str(raw))
            code = code_char_share(str(raw))
            noise_shares.append(share)
            code_shares.append(code)
            with_code += code > 0
            noisy = share > 0.5
            # The budget is spent on whatever is there, so the decision-relevant
            # quantity is everything that is not prose, code blocks included.
            non_prose = min(1.0, share + code) > 0.5
            majority_noise += noisy
            majority_non_prose += non_prose
            if len(cleaned) > budget:
                majority_non_prose_over += non_prose
                over.append(len(cleaned))
                blind = cleaned[:budget]
                truncated = truncate_at_boundary(cleaned, budget)
                kept.append(len(truncated))
                lost.append(budget - len(truncated))
                majority_noise_over += noisy
                # The old rule split a word whenever both sides of the cut are
                # non-blank; the new rule only still does so where no boundary was
                # reachable inside the keep fraction.
                mid_word = bool(cleaned[budget - 1].strip() and cleaned[budget].strip())
                cuts["mid_word_under_old_rule"] += mid_word
                if truncated == blind:
                    cuts["unchanged"] += 1
                    cuts["mid_word_remaining"] += mid_word
                elif truncated[-1] in ".!?\"')]":
                    cuts["sentence_boundary"] += 1
                else:
                    cuts["word_boundary"] += 1

    n = len(lengths)
    return TruncationMeasurement(
        source="mysql (untruncated Description_Text, Stage 0 markup stripping applied)",
        n_descriptions=n,
        budget=budget,
        over_budget=len(over),
        over_budget_share=round(len(over) / n, 4) if n else 0.0,
        length_percentiles=_percentiles(lengths),
        chars_kept_share_of_over_budget=(
            round(sum(kept) / sum(over), 4) if over else 0.0
        ),
        mid_word_cuts=cuts["mid_word_under_old_rule"],
        mid_word_cuts_remaining=cuts["mid_word_remaining"],
        sentence_boundary_cuts=cuts["sentence_boundary"],
        word_boundary_cuts=cuts["word_boundary"],
        unchanged_cuts=cuts["unchanged"],
        chars_lost_to_boundary_median=(sorted(lost)[len(lost) // 2] if lost else 0),
        majority_noise=majority_noise,
        majority_noise_share=round(majority_noise / n, 4) if n else 0.0,
        majority_noise_over_budget=majority_noise_over,
        mean_noise_char_share=(
            round(sum(noise_shares) / len(noise_shares), 4) if noise_shares else 0.0
        ),
        with_code_block=with_code,
        with_code_block_share=round(with_code / n, 4) if n else 0.0,
        mean_code_char_share=(
            round(sum(code_shares) / len(code_shares), 4) if code_shares else 0.0
        ),
        majority_non_prose=majority_non_prose,
        majority_non_prose_share=round(majority_non_prose / n, 4) if n else 0.0,
        majority_non_prose_over_budget=majority_non_prose_over,
    )


def render_truncation(measurement: TruncationMeasurement) -> list[str]:
    percentiles = " / ".join(
        f"{key} {value}" for key, value in measurement.length_percentiles.items()
    )
    return [
        "| Measure | Value |",
        "|---|---:|",
        f"| Descriptions with text | {measurement.n_descriptions} |",
        f"| Cleaned length (chars) | {percentiles} |",
        f"| Over the {measurement.budget}-char budget | {measurement.over_budget} "
        f"({measurement.over_budget_share * 100:.1f}%) |",
        f"| Share of those descriptions' characters kept | "
        f"{measurement.chars_kept_share_of_over_budget * 100:.1f}% |",
        f"| Cuts the old blind slice made mid-word | {measurement.mid_word_cuts} |",
        f"| Cuts the new rule takes at a sentence end | "
        f"{measurement.sentence_boundary_cuts} |",
        f"| Cuts the new rule moves back to a word end | "
        f"{measurement.word_boundary_cuts} |",
        f"| Cuts the new rule leaves where they were | {measurement.unchanged_cuts} |",
        f"| ... of those, still mid-word (no boundary in range) | "
        f"{measurement.mid_word_cuts_remaining} |",
        f"| Median characters given up to reach that boundary | "
        f"{measurement.chars_lost_to_boundary_median} |",
        f"| Majority log/boilerplate descriptions (>50% of chars) | "
        f"{measurement.majority_noise} ({measurement.majority_noise_share * 100:.1f}%) |",
        f"| ... of them, over budget | {measurement.majority_noise_over_budget} |",
        f"| Mean share of a description that is log/boilerplate | "
        f"{measurement.mean_noise_char_share * 100:.1f}% |",
        f"| Descriptions still carrying a {{code}}/{{noformat}} block | "
        f"{measurement.with_code_block} "
        f"({measurement.with_code_block_share * 100:.1f}%) |",
        f"| Mean share of a description inside such a block | "
        f"{measurement.mean_code_char_share * 100:.1f}% |",
        f"| Majority non-prose (log/boilerplate + code > 50%) | "
        f"{measurement.majority_non_prose} "
        f"({measurement.majority_non_prose_share * 100:.1f}%) |",
        f"| ... of them, over budget | {measurement.majority_non_prose_over_budget} |",
    ]


# ---------- shared: the contribution corpus ----------

def load_normalized(path: Path | None = None) -> list[Contribution]:
    path = NORMALIZED_PATH if path is None else path
    with path.open(encoding="utf-8") as handle:
        return [Contribution.model_validate_json(line) for line in handle if line.strip()]


# ---------- G3a: vocabulary document frequency ----------

@dataclass(frozen=True)
class VocabularyMeasurement:
    kind: str
    canonicals: int
    raw_terms: int
    people: int
    df_histogram: dict[str, int]
    survivors_by_floor: dict[str, int]
    support_on_survivors_by_floor: dict[str, float]


def raw_term_counts(path: Path | None = None) -> Counter[str]:
    """Raw terms per kind before Stage 3 merged them: canonicals plus their aliases."""
    path = TERMS_PATH if path is None else path
    totals: Counter[str] = Counter()
    if not path.exists():
        return totals
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                totals[str(record["kind"])] += 1 + len(record.get("aliases") or [])
    return totals


def measure_vocabulary(
    contribs: Sequence[Contribution], *, floors: Sequence[int] = (2, 3, 5, 10)
) -> list[VocabularyMeasurement]:
    """Document frequency per canonical term, and what each candidate floor leaves.

    Document frequency is distinct contributions, not mentions. Survivors are what the
    Stage 3 gate keeps canonical at each floor; the terms below it do not disappear —
    they attach to their nearest survivor as aliases, so every mention still resolves.
    *Support on survivors* is the share of all contribution-term support that still has
    a canonical of its own rather than being folded into a neighbour's.
    """
    out: list[VocabularyMeasurement] = []
    raw = raw_term_counts()
    for kind, refs in (
        ("skill", [[s.name for s in c.skills] for c in contribs]),
        ("specialization", [[s.name for s in c.specializations] for c in contribs]),
    ):
        df: Counter[str] = Counter()
        owners: dict[str, set[str]] = defaultdict(set)
        for contribution, terms in zip(contribs, refs, strict=True):
            for term in set(terms):
                df[term] += 1
                owners[term].add(contribution.person_id)
        histogram: Counter[str] = Counter()
        for count in df.values():
            bucket = str(count) if count < 5 else ("5-9" if count < 10 else "10+")
            histogram[bucket] += 1
        total_df = sum(df.values())
        out.append(
            VocabularyMeasurement(
                kind=kind,
                canonicals=len(df),
                raw_terms=raw.get(kind, 0),
                people=len({person for people in owners.values() for person in people}),
                df_histogram={
                    key: histogram[key]
                    for key in ("1", "2", "3", "4", "5-9", "10+")
                    if histogram[key]
                },
                survivors_by_floor={
                    str(floor): sum(1 for count in df.values() if count >= floor)
                    for floor in floors
                },
                support_on_survivors_by_floor={
                    str(floor): round(
                        sum(count for count in df.values() if count >= floor) / total_df, 4
                    )
                    for floor in floors
                },
            )
        )
    return out


def render_vocabulary(rows: Sequence[VocabularyMeasurement]) -> list[str]:
    floors = sorted({int(key) for row in rows for key in row.survivors_by_floor})
    lines = [
        "| Vocabulary | Raw terms | Canonical terms (floor off) | "
        + " | ".join(f"df ≥ {floor}" for floor in floors)
        + " |",
        "|---|---:|---:|" + "---:|" * len(floors),
    ]
    for row in rows:
        lines.append(
            f"| {row.kind} | {row.raw_terms} | {row.canonicals} | "
            + " | ".join(str(row.survivors_by_floor[str(floor)]) for floor in floors)
            + " |"
        )
    lines += ["", "| Vocabulary | " + " | ".join(
        f"df = {key}" if key.isdigit() else f"df {key}"
        for key in ("1", "2", "3", "4", "5-9", "10+")
    ) + " |", "|---|" + "---:|" * 6]
    for row in rows:
        lines.append(
            f"| {row.kind} | "
            + " | ".join(
                str(row.df_histogram.get(key, 0))
                for key in ("1", "2", "3", "4", "5-9", "10+")
            )
            + " |"
        )
    return lines


# ---------- G11a: activity currency ----------

def last_activity(contribs: Sequence[Contribution]) -> dict[str, date]:
    """Person -> the end of the last quarter they have a retained contribution in."""
    latest: dict[str, date] = {}
    for contribution in contribs:
        end = period_end(contribution.period)
        if end > latest.get(contribution.person_id, date.min):
            latest[contribution.person_id] = end
    return latest


@dataclass(frozen=True)
class ActivityMeasurement:
    people: int
    as_of: str
    half_life_days: int
    quarters_histogram: dict[str, int]
    quantiles: dict[str, float]
    decay_quantiles: dict[str, float]
    stale_beyond_8_quarters: int
    stale_beyond_12_quarters: int


def measure_activity(
    contribs: Sequence[Contribution], *, as_of: date | None = None
) -> ActivityMeasurement:
    """Quarters since each person's last contribution, measured at a fixed snapshot.

    The snapshot is the holdout cutoff, the same date the graph's stored decay is frozen
    at, so this is the distribution the shipped graph would rank on today.
    """
    as_of = snapshot_date() if as_of is None else as_of
    half_life = int(settings["projections.recency_half_life_days"])
    latest = last_activity(contribs)
    gaps = sorted(
        max(0, int((as_of - last).days // 91)) for last in latest.values()
    )
    decays = sorted(decay(last, half_life, as_of=as_of) for last in latest.values())
    histogram: Counter[str] = Counter()
    for gap in gaps:
        bucket = (
            "0-1" if gap <= 1 else
            "2-3" if gap <= 3 else
            "4-7" if gap <= 7 else
            "8-11" if gap <= 11 else
            "12+"
        )
        histogram[bucket] += 1

    def quantile(values: Sequence[float], q: float) -> float:
        return round(float(values[min(len(values) - 1, int(q * (len(values) - 1)))]), 4)

    return ActivityMeasurement(
        people=len(latest),
        as_of=as_of.isoformat(),
        half_life_days=half_life,
        quarters_histogram={
            key: histogram[key]
            for key in ("0-1", "2-3", "4-7", "8-11", "12+")
            if histogram[key]
        },
        quantiles={
            "p10": quantile(gaps, 0.10), "p50": quantile(gaps, 0.50),
            "p90": quantile(gaps, 0.90), "max": float(gaps[-1]) if gaps else 0.0,
        },
        decay_quantiles={
            "p10": quantile(decays, 0.10), "p50": quantile(decays, 0.50),
            "p90": quantile(decays, 0.90),
        },
        stale_beyond_8_quarters=sum(1 for gap in gaps if gap >= 8),
        stale_beyond_12_quarters=sum(1 for gap in gaps if gap >= 12),
    )


def render_activity(measurement: ActivityMeasurement) -> list[str]:
    return [
        "| Measure | Value |",
        "|---|---:|",
        f"| People with a retained contribution | {measurement.people} |",
        f"| Measured at | {measurement.as_of} |",
        *(
            f"| Quarters since last contribution: {key} | {value} |"
            for key, value in measurement.quarters_histogram.items()
        ),
        f"| Median quarters idle | {measurement.quantiles['p50']:.0f} |",
        f"| 90th percentile quarters idle | {measurement.quantiles['p90']:.0f} |",
        f"| Longest gap (quarters) | {measurement.quantiles['max']:.0f} |",
        f"| Idle two years or more (≥ 8 quarters) | "
        f"{measurement.stale_beyond_8_quarters} |",
        f"| Idle three years or more (≥ 12 quarters) | "
        f"{measurement.stale_beyond_12_quarters} |",
        f"| Activity decay at the cutoff (p10 / p50 / p90) | "
        f"{measurement.decay_quantiles['p10']:.3f} / "
        f"{measurement.decay_quantiles['p50']:.3f} / "
        f"{measurement.decay_quantiles['p90']:.3f} |",
    ]


# ---------- G5 / G6 / G11a: offline re-score of the checkpointed components ----------

# Recorded settings that can change a checkpointed score *component*. Everything else a
# sidecar records is re-rank-side — which candidate view, how wide the window — and
# cannot: the components are produced by retrieval and expansion, before the re-rank
# exists. Reading a checkpoint across a re-rank-side change is therefore sound, and this
# tuple is what makes that judgment explicit instead of implied.
COMPONENT_RELEVANT_KEYS = (
    "manifest_version",
    "holdout_cutoff",
    "intent_model",
    "embedding_model",
    "recency_half_life_days",
)
COMPONENT_RELEVANT_RETRIEVAL_KEYS = (
    "vector_top_k",
    "structured_top_k",
    "bm25_top_k",
    "roster_vector_pool_k",
    "contributions_per_person",
)


def checkpoint_drift(path: Path) -> list[str]:
    """Recorded settings that differ from the live ones, refusing component-relevant ones."""
    sidecar = config_path("validation", path=path)
    if not sidecar.exists():
        return []
    recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    current = retrieval_config()
    drift: list[str] = []
    blocking: list[str] = []
    for key in sorted(set(recorded) | set(current)):
        if key == "retrieval":
            for inner in sorted(set(recorded.get(key, {})) | set(current.get(key, {}))):
                if recorded.get(key, {}).get(inner) != current.get(key, {}).get(inner):
                    label = (
                        f"retrieval.{inner}: {recorded.get(key, {}).get(inner)!r} -> "
                        f"{current.get(key, {}).get(inner)!r}"
                    )
                    drift.append(label)
                    if inner in COMPONENT_RELEVANT_RETRIEVAL_KEYS:
                        blocking.append(label)
        elif recorded.get(key) != current.get(key):
            label = f"{key}: {recorded.get(key)!r} -> {current.get(key)!r}"
            drift.append(label)
            if key in COMPONENT_RELEVANT_KEYS:
                blocking.append(label)
    if blocking:
        raise SystemExit(
            f"{sidecar} was written under settings that change the score components "
            f"themselves ({'; '.join(blocking)}). Re-scoring it would compare two "
            "different retrievals; restore those settings or re-dump the split."
        )
    return drift


def load_components(path: Path | None = None) -> tuple[list[CaseScores], list[str]]:
    """The validation score-component checkpoint, plus its re-rank-side drift."""
    path = V3_SCORES if path is None else path
    if not path.exists():
        raise SystemExit(f"no score checkpoint at {path}")
    drift = checkpoint_drift(path)
    cases: dict[str, CaseScores] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                case = CaseScores.from_json(json.loads(line))
                cases[case.issue_id] = case
    return [cases[issue_id] for issue_id in sorted(cases)], drift


@dataclass(frozen=True)
class PersonSignals:
    """The person-level stand-ins the re-score joins onto each checkpointed candidate."""

    confidence_mean: float
    confidence_max: float
    confidence_min: float
    primary_share: float
    last_activity: date | None


def person_signals(contribs: Sequence[Contribution]) -> dict[str, PersonSignals]:
    """Per person: their confidence profile, primary share, and last activity."""
    confidence: dict[str, list[float]] = defaultdict(list)
    primary: Counter[str] = Counter()
    specialization_refs: Counter[str] = Counter()
    for contribution in contribs:
        confidence[contribution.person_id].append(
            improvements.confidence_value(contribution.confidence)
        )
        for ref in contribution.specializations:
            specialization_refs[contribution.person_id] += 1
            if ref.strength == "primary":
                primary[contribution.person_id] += 1
    latest = last_activity(contribs)
    return {
        person_id: PersonSignals(
            confidence_mean=sum(values) / len(values),
            confidence_max=max(values),
            confidence_min=min(values),
            primary_share=(
                primary[person_id] / specialization_refs[person_id]
                if specialization_refs[person_id]
                else 0.0
            ),
            last_activity=latest.get(person_id),
        )
        for person_id, values in confidence.items()
    }


def _transform(
    cases: Sequence[CaseScores], change
) -> list[CaseScores]:
    """Rebuild the checkpoint with ``change(person_id, case, parts) -> parts`` applied."""
    return [
        CaseScores(
            issue_id=case.issue_id,
            issue_key=case.issue_key,
            project_key=case.project_key,
            truth=case.truth,
            roles=tuple(
                RoleScores(
                    role=role.role,
                    parts={
                        person_id: change(person_id, case, dict(parts))
                        for person_id, parts in role.parts.items()
                    },
                    sources=dict(role.sources),
                )
                for role in case.roles
            ),
        )
        for case in cases
    ]


def _per_case(cases: Sequence[CaseScores], weights: Mapping[str, float]) -> dict:
    """Per-case Hit@K and reciprocal rank of the score-only ranking."""
    out: dict[str, dict[str, float]] = {}
    for case in cases:
        ranked = case.ordering(dict(weights))
        truth = set(case.truth)
        out[case.issue_id] = {
            **{f"hit_at_{k}": hit_at_k(ranked, truth, k) for k in HIT_KS},
            "mrr": mrr(ranked, truth),
        }
    return out


@dataclass(frozen=True)
class RescoreArm:
    """One flag setting re-scored against the unchanged checkpoint."""

    name: str
    note: str
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    mrr: float
    window_recall: float
    moved_cases: int
    largest_delta: float


def rescore_arms(
    cases: Sequence[CaseScores],
    signals: Mapping[str, PersonSignals],
    as_of: Mapping[str, date],
) -> tuple[list[RescoreArm], dict[str, dict]]:
    """Every wave-1 ranking flag, re-scored offline through the engine's arithmetic.

    Returns the summary rows and, per arm, the per-case metrics the paired tables need.
    """
    base_weights = dict(settings["scoring.weights"])
    top_k = int(settings["retrieval.rerank_top_k"])
    half_life = int(settings["projections.recency_half_life_days"])
    confidence_weight = improvements.confidence_weight()
    activity_weight = improvements.activity_currency_weight()
    secondary = improvements.secondary_weight()

    def confidence_of(person_id: str, field_: str) -> float:
        signal = signals.get(person_id)
        return getattr(signal, field_) if signal else 1.0

    def with_confidence(field_: str, component: bool):
        def change(person_id, _case, parts):
            value = confidence_of(person_id, field_)
            if component:
                parts[improvements.CONFIDENCE_COMPONENT] = value
            else:
                parts["evidence_strength"] = parts.get("evidence_strength", 0.0) * value
            return parts

        return change

    def with_strength(factor):
        def change(person_id, _case, parts):
            if "specialization_match" in parts:
                parts["specialization_match"] *= factor(person_id)
            return parts

        return change

    def with_activity(person_id, case, parts):
        signal = signals.get(person_id)
        moment = as_of.get(case.issue_id)
        parts[improvements.ACTIVITY_COMPONENT] = (
            round(decay(signal.last_activity, half_life, as_of=moment), 4)
            if signal and signal.last_activity and moment
            else 0.0
        )
        return parts

    confidence_weights = {**base_weights, improvements.CONFIDENCE_COMPONENT: confidence_weight}
    activity_weights = {**base_weights, improvements.ACTIVITY_COMPONENT: activity_weight}

    # Scaling a component uniformly is close to lowering its weight, and the v2 sweep
    # already found `specialization_match` wanted less weight. The control arm applies
    # the *average* strength credit to everyone, so whatever the person-varying arm does
    # beyond this row is the strength label doing work rather than the down-weighting.
    credits = [improvements.strength_credit(signal.primary_share) for signal in signals.values()]
    mean_credit = sum(credits) / len(credits) if credits else 1.0

    plan = [
        ("baseline (all flags off)", "the checkpoint as it stands", cases, base_weights),
        (
            "G5 component, mean confidence",
            f"confidence as a fifth component at weight {confidence_weight}",
            _transform(cases, with_confidence("confidence_mean", True)),
            confidence_weights,
        ),
        (
            "G5 component, sensitivity: best case",
            "every person at their highest-confidence evidence",
            _transform(cases, with_confidence("confidence_max", True)),
            confidence_weights,
        ),
        (
            "G5 component, sensitivity: worst case",
            "every person at their lowest-confidence evidence",
            _transform(cases, with_confidence("confidence_min", True)),
            confidence_weights,
        ),
        (
            "G5 multiplier, mean confidence",
            "confidence scales evidence_strength instead of competing for weight",
            _transform(cases, with_confidence("confidence_mean", False)),
            base_weights,
        ),
        (
            "G6 strength, person primary share",
            f"matched specializations scaled between {secondary} and 1.0",
            _transform(
                cases,
                with_strength(
                    lambda person_id: improvements.strength_credit(
                        signals[person_id].primary_share if person_id in signals else 1.0
                    )
                ),
            ),
            base_weights,
        ),
        (
            "G6 control, constant scale at the mean credit",
            "the same average scale for everyone: isolates plain down-weighting",
            _transform(cases, with_strength(lambda _person_id: mean_credit)),
            base_weights,
        ),
        (
            "G6 strength, sensitivity: all secondary",
            f"every matched specialization counts {secondary}",
            _transform(cases, with_strength(lambda _person_id: secondary)),
            base_weights,
        ),
        (
            "G11a activity currency (exact)",
            f"activity decay as a fifth component at weight {activity_weight}",
            _transform(cases, with_activity),
            activity_weights,
        ),
    ]

    baseline_per_case = _per_case(cases, base_weights)
    rows: list[RescoreArm] = []
    per_case: dict[str, dict] = {}
    for name, note, arm_cases, weights in plan:
        summary = evaluate_weights(arm_cases, dict(weights), top_k=top_k)
        metrics = _per_case(arm_cases, weights)
        per_case[name] = metrics
        moved = sum(
            1
            for issue_id, values in metrics.items()
            if any(
                abs(values[metric] - baseline_per_case[issue_id][metric]) > 1e-9
                for metric in (*[f"hit_at_{k}" for k in HIT_KS], "mrr")
            )
        )
        largest = max(
            abs(getattr(summary, metric) - getattr(rows[0], metric)) if rows else 0.0
            for metric in ("hit_at_1", "hit_at_5", "hit_at_10", "mrr")
        )
        rows.append(
            RescoreArm(
                name=name,
                note=note,
                hit_at_1=summary.hit_at_1,
                hit_at_5=summary.hit_at_5,
                hit_at_10=summary.hit_at_10,
                mrr=summary.mrr,
                window_recall=summary.window_recall,
                moved_cases=moved,
                largest_delta=round(largest, 4),
            )
        )
    return rows, per_case


def render_rescore(rows: Sequence[RescoreArm]) -> list[str]:
    lines = [
        "| Arm | Hit@1 | Hit@5 | Hit@10 | MRR | Window recall | Cases moved | "
        "Largest Δ vs baseline |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.name} | {row.hit_at_1:.3f} | {row.hit_at_5:.3f} | "
            f"{row.hit_at_10:.3f} | {row.mrr:.3f} | {row.window_recall:.3f} | "
            f"{row.moved_cases} | {row.largest_delta:.3f} |"
        )
    return lines


def render_rescore_paired(per_case: Mapping[str, dict], baseline: str) -> list[str]:
    """Paired per-case tables for every arm against the flags-off baseline."""
    lines: list[str] = []
    before = per_case[baseline]
    for name, after in per_case.items():
        if name == baseline:
            continue
        binary = [
            paired_binary(
                f"Hit@{k}",
                {case: values[f"hit_at_{k}"] for case, values in before.items()},
                {case: values[f"hit_at_{k}"] for case, values in after.items()},
            )
            for k in HIT_KS
        ]
        continuous = [
            paired_bootstrap(
                "MRR",
                {case: values["mrr"] for case, values in before.items()},
                {case: values["mrr"] for case, values in after.items()},
            )
        ]
        lines += ["", f"**{name}**", "", *render_paired(binary, continuous)]
    return lines


# ---------- acceptance: baseline parity with every flag off ----------

PARITY_DIR = DATA_DIR / "eval" / "wave1" / "parity"
BASELINE_SYSTEMS = ("bm25", "vector_only", "most_active")


def check_baseline_parity(
    split: str = "validation", *, target: Path | None = None
) -> dict[str, object]:
    """Re-run the three deterministic baselines and diff them against the frozen v3 run.

    The acceptance criterion for this order: with every improvement flag off, benchmark
    behaviour is unchanged. The baselines are the part of the harness that is fully
    deterministic and needs no model call, so re-running them is a real re-execution
    rather than a re-reading of the same file — and their rankings and candidate pools
    have to come back identical, element for element.

    The configuration digest is checked alongside, because that is what decides whether
    a checkpoint may be extended at all: if the flags had leaked into it, every frozen
    namespace would have become unreadable.
    """
    from .run_eval import checkpoint_path, config_digest, load_checkpoint, run_split
    from .run_v3 import runs_dir, v3_config

    target = PARITY_DIR if target is None else target
    config = v3_config(split)
    digest = config_digest(config)
    frozen_dir = runs_dir(str(settings["eval.v3.frozen_validation_variant"]))
    frozen = load_checkpoint(split, runs_dir=frozen_dir)
    recorded = sorted({record["config_digest"] for record in frozen.values()})

    checkpoint_path(split, runs_dir=target).unlink(missing_ok=True)
    run_split(
        split,
        systems=BASELINE_SYSTEMS,
        stage=str(config["stage"]),
        runs_dir=target,
        config=config,
    )
    replayed = load_checkpoint(split, runs_dir=target)

    compared = 0
    mismatches: list[str] = []
    for (system, issue_id), record in sorted(replayed.items()):
        reference = frozen.get((system, issue_id))
        if reference is None:
            mismatches.append(f"{system}/{issue_id}: absent from the frozen run")
            continue
        compared += 1
        for field_ in ("ranked_ids", "candidate_ids"):
            if record.get(field_) != reference.get(field_):
                mismatches.append(f"{system}/{issue_id}: {field_} differs")
    return {
        "split": split,
        "systems": list(BASELINE_SYSTEMS),
        "frozen_namespace": str(frozen_dir),
        "records_compared": compared,
        "mismatches": mismatches,
        "identical": not mismatches,
        "current_config_digest": digest,
        "frozen_config_digests": recorded,
        "digest_unchanged": recorded == [digest],
        "improvement_flags_enabled": improvements.enabled(),
    }


def render_parity(result: Mapping[str, object]) -> list[str]:
    return [
        "| Check | Result |",
        "|---|---|",
        f"| Baselines re-run | {', '.join(result['systems'])} on the "
        f"{result['split']} split |",
        f"| Records compared (ranking and candidate pool) | "
        f"{result['records_compared']} |",
        f"| Byte-identical to the frozen v3 run | "
        f"{'yes' if result['identical'] else 'NO: ' + '; '.join(result['mismatches'])} |",
        f"| Configuration digest now | `{result['current_config_digest']}` |",
        f"| Digest recorded in the frozen checkpoint | "
        f"{', '.join(f'`{value}`' for value in result['frozen_config_digests'])} |",
        f"| Improvement flags on | "
        f"{result['improvement_flags_enabled'] or 'none — every flag is at its default'} |",
    ]


# ---------- CLI ----------

def _write(name: str, payload: object) -> Path:
    WAVE1_DIR.mkdir(parents=True, exist_ok=True)
    path = WAVE1_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")
    print(f"wrote {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wave-1 offline measurements ($0, no LLM)")
    parser.add_argument("--truncation", action="store_true", help="G1 (reads MySQL)")
    parser.add_argument("--vocabulary", action="store_true", help="G3a")
    parser.add_argument("--activity", action="store_true", help="G11a distribution")
    parser.add_argument("--rescore", action="store_true", help="G5/G6/G11a re-score")
    parser.add_argument("--parity", action="store_true", help="acceptance: baselines unchanged")
    parser.add_argument("--all", action="store_true", help="every measurement above")
    args = parser.parse_args(argv)
    wanted = {
        name: getattr(args, name) or args.all
        for name in ("truncation", "vocabulary", "activity", "rescore", "parity")
    }
    if not any(wanted.values()):
        parser.error("nothing to do: pass one of --truncation/--vocabulary/--activity/"
                     "--rescore/--parity, or --all")

    contribs: list[Contribution] | None = None
    if wanted["vocabulary"] or wanted["activity"] or wanted["rescore"]:
        contribs = load_normalized()
        print(f"{len(contribs)} normalized contributions loaded\n")

    if wanted["truncation"]:
        measurement = measure_truncation()
        print("## G1 — description truncation\n")
        print("\n".join(render_truncation(measurement)) + "\n")
        _write("truncation", asdict(measurement))

    if wanted["vocabulary"]:
        rows = measure_vocabulary(contribs)
        print("## G3a — vocabulary document frequency\n")
        print("\n".join(render_vocabulary(rows)) + "\n")
        _write("vocabulary", [asdict(row) for row in rows])

    if wanted["activity"]:
        measurement = measure_activity(contribs)
        print("## G11a — activity currency\n")
        print("\n".join(render_activity(measurement)) + "\n")
        _write("activity", asdict(measurement))

    if wanted["rescore"]:
        cases, drift = load_components()
        as_of = {
            case.issue_id: query_context(case).as_of_time.date()
            for case in load_manifest(splits=("validation",))
        }
        rows, per_case = rescore_arms(cases, person_signals(contribs), as_of)
        print("## G5 / G6 / G11a — offline re-score\n")
        if drift:
            print("Checkpoint drift (re-rank-side only, cannot change a component): "
                  + "; ".join(drift) + "\n")
        print("\n".join(render_rescore(rows)) + "\n")
        print("\n".join(render_rescore_paired(per_case, rows[0].name)) + "\n")
        _write(
            "rescore",
            {
                "checkpoint": str(V3_SCORES),
                "checkpoint_drift": drift,
                "n_cases": len(cases),
                "run_to_run_floor": RUN_TO_RUN_FLOOR,
                "one_case_in_hit_at_1": round(1 / len(cases), 4) if cases else 0.0,
                "components": list(SCORE_COMPONENTS),
                "arms": [asdict(row) for row in rows],
            },
        )

    if wanted["parity"]:
        result = check_baseline_parity()
        print("## Acceptance — baselines with every flag off\n")
        print("\n".join(render_parity(result)) + "\n")
        _write("parity", result)
        if not result["identical"] or not result["digest_unchanged"]:
            return 1
    return 0


__all__ = [
    "ActivityMeasurement",
    "PersonSignals",
    "RescoreArm",
    "TruncationMeasurement",
    "VocabularyMeasurement",
    "check_baseline_parity",
    "checkpoint_drift",
    "is_noise_line",
    "last_activity",
    "load_components",
    "measure_activity",
    "measure_truncation",
    "measure_vocabulary",
    "noise_char_share",
    "person_signals",
    "rescore_arms",
]


if __name__ == "__main__":
    raise SystemExit(main())
