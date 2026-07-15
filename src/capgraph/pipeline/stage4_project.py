"""Stage 4: normalized.jsonl -> capabilities.jsonl (models.PersonCapability per line).

Aggregates contributions into per-person HAS_SKILL / HAS_SPECIALIZATION payloads with
evidence counts, last-used dates, and exponential recency decay.
"""
from __future__ import annotations

import math
from calendar import monthrange
from collections import defaultdict
from datetime import date

from ..models import Contribution, PersonCapability
from ..settings import DATA_DIR, settings

NORM_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
CAPS_PATH = DATA_DIR / "contributions" / "capabilities.jsonl"


def period_end(period: str) -> date:
    """Return the actual final calendar day represented by ``YYYY-QN``."""
    year, q = period.split("-Q")
    end_month = int(q) * 3
    return date(int(year), end_month, monthrange(int(year), end_month)[1])


def decay(last_used: date, half_life_days: int, *, as_of: date) -> float:
    """Calculate deterministic recency at the graph/query snapshot date."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    days = (as_of - last_used).days
    return math.exp(-math.log(2) * max(days, 0) / half_life_days)


def build_capabilities(
    contribs: list[Contribution],
    *,
    as_of: date | None = None,
) -> list[PersonCapability]:
    """Build projections as of a fixed snapshot, never relative to wall-clock time.

    ``as_of`` defaults to the configured holdout cutoff for backwards compatibility.
    Callers evaluating later query snapshots should pass their query time explicitly.
    Quarter-level contributions cannot identify an exact last-used day, so a quarter
    that is not wholly before the snapshot is rejected rather than relabelled.
    """
    if as_of is None:
        as_of = date.fromisoformat(settings["dataset.holdout_cutoff"])
    half_life = settings["projections.recency_half_life_days"]
    acc: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "ids": [], "last": date.min})
    for c in contribs:
        end = period_end(c.period)
        if end >= as_of:
            raise ValueError(
                f"contribution {c.contribution_id} ({c.period}) is not wholly before "
                f"snapshot {as_of.isoformat()}"
            )
        for kind, refs in [("skill", c.skills), ("specialization", c.specializations)]:
            for ref in refs:
                key = (c.person_id, ref.name, kind)
                acc[key]["count"] += 1
                acc[key]["ids"].append(c.contribution_id)
                acc[key]["last"] = max(acc[key]["last"], end)
    return [
        PersonCapability(
            person_id=pid, term=term, kind=kind,
            evidence_count=v["count"], contribution_ids=v["ids"],
            last_used=v["last"],
            decay_score=round(decay(v["last"], half_life, as_of=as_of), 4),
        )
        for (pid, term, kind), v in acc.items()
    ]


def main() -> None:
    with NORM_PATH.open(encoding="utf-8") as handle:
        contribs = [Contribution.model_validate_json(line) for line in handle]
    as_of = date.fromisoformat(settings["dataset.holdout_cutoff"])
    caps = build_capabilities(contribs, as_of=as_of)
    with open(CAPS_PATH, "w") as f:
        for cap in caps:
            f.write(cap.model_dump_json() + "\n")
    print(f"{len(caps)} person-capability edges -> {CAPS_PATH}")


if __name__ == "__main__":
    main()
