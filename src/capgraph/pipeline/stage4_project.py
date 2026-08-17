"""Stage 4: normalized.jsonl -> capabilities.jsonl (models.PersonCapability per line).

Aggregates contributions into per-person HAS_SKILL / HAS_SPECIALIZATION payloads with
evidence counts, last-used dates, and exponential recency decay.

Two invariants hold the temporal contract together:

- Recency is measured from an explicit snapshot date — the configured
  ``dataset.holdout_cutoff`` by default, a query time when the caller passes one —
  never from wall-clock time. A projection rebuilt next year is byte-identical.
- A quarter-level contribution cannot identify an exact last-used day, so a period
  that is not wholly before the snapshot is rejected rather than relabelled.

Output is fully sorted (rows by person/kind/term, evidence ids within a row), so
re-running over the same normalized.jsonl rewrites byte-identical bytes.
"""
from __future__ import annotations

import math
import re
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..models import Contribution, PersonCapability
from ..settings import DATA_DIR, settings

NORM_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
CAPS_PATH = DATA_DIR / "contributions" / "capabilities.jsonl"

PERIOD_PATTERN = re.compile(r"^(\d{4})-Q([1-4])$")


def period_end(period: str) -> date:
    """Return the actual final calendar day represented by ``YYYY-QN``."""
    match = PERIOD_PATTERN.match(period)
    if match is None:
        raise ValueError(f"expected a YYYY-QN quarter period, got {period!r}")
    year, quarter = int(match.group(1)), int(match.group(2))
    end_month = quarter * 3
    return date(year, end_month, monthrange(year, end_month)[1])


def snapshot_date() -> date:
    """The frozen projection snapshot. Wall-clock time is never an input here."""
    return date.fromisoformat(settings["dataset.holdout_cutoff"])


def decay(last_used: date, half_life_days: int, *, as_of: date) -> float:
    """Calculate deterministic recency at the graph/query snapshot date."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    days = (as_of - last_used).days
    return math.exp(-math.log(2) * max(days, 0) / half_life_days)


@dataclass
class _Evidence:
    """Accumulator for one (person, kind, term) edge."""

    contribution_ids: set[str] = field(default_factory=set)
    # Backlog G6: the subset of those contributions that called the specialization
    # "primary". Kept as ids, not a counter, for the same reason the set above is: a
    # contribution mentioning the term twice must not count twice.
    primary_contribution_ids: set[str] = field(default_factory=set)
    last_used: date = date.min


def build_capabilities(
    contribs: list[Contribution],
    *,
    as_of: date | None = None,
) -> list[PersonCapability]:
    """Build projections as of a fixed snapshot, never relative to wall-clock time.

    ``as_of`` defaults to the configured holdout cutoff for backwards compatibility.
    Callers evaluating later query snapshots should pass their query time explicitly.
    Evidence is counted per contribution: Stage 3 already dedupes terms within a
    contribution, and the set here keeps a repeated mention from inflating the count.
    """
    if as_of is None:
        as_of = snapshot_date()
    half_life = int(settings["projections.recency_half_life_days"])
    acc: dict[tuple[str, str, str], _Evidence] = defaultdict(_Evidence)
    for c in contribs:
        end = period_end(c.period)
        if end >= as_of:
            raise ValueError(
                f"contribution {c.contribution_id} ({c.period}) is not wholly before "
                f"snapshot {as_of.isoformat()}"
            )
        for kind, refs in [("skill", c.skills), ("specialization", c.specializations)]:
            for ref in refs:
                evidence = acc[(c.person_id, kind, ref.name)]
                evidence.contribution_ids.add(c.contribution_id)
                if getattr(ref, "strength", None) == "primary":
                    evidence.primary_contribution_ids.add(c.contribution_id)
                evidence.last_used = max(evidence.last_used, end)
    return [
        PersonCapability(
            person_id=person_id, term=term, kind=kind,
            evidence_count=len(evidence.contribution_ids),
            contribution_ids=sorted(evidence.contribution_ids),
            last_used=evidence.last_used,
            decay_score=round(decay(evidence.last_used, half_life, as_of=as_of), 4),
            primary_evidence_count=len(evidence.primary_contribution_ids),
        )
        for (person_id, kind, term), evidence in sorted(acc.items())
    ]


def load_contributions(path: Path | None = None) -> list[Contribution]:
    path = NORM_PATH if path is None else path
    with path.open(encoding="utf-8") as handle:
        return [Contribution.model_validate_json(line) for line in handle if line.strip()]


def write_capabilities(caps: list[PersonCapability], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for cap in caps:
            handle.write(cap.model_dump_json() + "\n")


def _quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile of an already-sorted list; no numpy, no interpolation."""
    rank = max(1, math.ceil(q * len(values)))
    return values[rank - 1]


def summarize(caps: list[PersonCapability], *, as_of: date) -> str:
    """Deterministic run summary for the stage log and the acceptance report."""
    lines = [f"snapshot (as_of): {as_of.isoformat()}", f"edges: {len(caps)}"]
    for kind in ("skill", "specialization"):
        rows = [cap for cap in caps if cap.kind == kind]
        terms = {cap.term for cap in rows}
        people = {cap.person_id for cap in rows}
        lines.append(
            f"  {kind}: {len(rows)} edges, {len(terms)} distinct terms, {len(people)} people"
        )
    lines.append(f"people covered: {len({cap.person_id for cap in caps})}")
    if not caps:
        return "\n".join(lines)

    scores = sorted(cap.decay_score for cap in caps)
    lines.append(
        "decay_score: min {:.4f} p25 {:.4f} median {:.4f} p75 {:.4f} max {:.4f} mean {:.4f}".format(
            scores[0], _quantile(scores, 0.25), _quantile(scores, 0.50),
            _quantile(scores, 0.75), scores[-1], sum(scores) / len(scores),
        )
    )
    counts = sorted(cap.evidence_count for cap in caps)
    lines.append(
        f"evidence_count: min {counts[0]} median {_quantile([float(c) for c in counts], 0.50):.0f} "
        f"max {counts[-1]}"
    )
    last_used = sorted(cap.last_used for cap in caps)
    lines.append(f"last_used: {last_used[0].isoformat()} .. {last_used[-1].isoformat()}")

    specs = [cap for cap in caps if cap.kind == "specialization"]
    if specs:
        # Backlog G6: how much of the specialization projection the strength label can
        # actually separate. An edge is "all primary" when every supporting contribution
        # called it primary, "all secondary" when none did, mixed otherwise.
        all_primary = sum(cap.primary_evidence_count == cap.evidence_count for cap in specs)
        none_primary = sum(cap.primary_evidence_count == 0 for cap in specs)
        lines.append(
            f"specialization strength: {all_primary} all-primary, {none_primary} "
            f"all-secondary, {len(specs) - all_primary - none_primary} mixed edges"
        )
    return "\n".join(lines)


def run(
    *, norm_path: Path | None = None, caps_path: Path | None = None
) -> list[PersonCapability]:
    """Project one normalized namespace into one capability namespace.

    Paths default to the production artifacts, so ``main()`` is unchanged. They are
    parameters for the same reason Stage 3's are: the G3a sweep
    (``docs/work-orders/deterministic-sweeps.md``) projects a *second*, gated vocabulary
    and must not write over ``data/contributions/capabilities.jsonl``.
    """
    caps_path = CAPS_PATH if caps_path is None else caps_path
    contribs = load_contributions(norm_path)
    as_of = snapshot_date()
    caps = build_capabilities(contribs, as_of=as_of)
    caps_path.parent.mkdir(parents=True, exist_ok=True)
    write_capabilities(caps, caps_path)
    print(f"{len(contribs)} contributions -> {len(caps)} person-capability edges -> {caps_path}")
    print(summarize(caps, as_of=as_of))
    return caps


def main() -> None:
    run()


if __name__ == "__main__":
    main()
