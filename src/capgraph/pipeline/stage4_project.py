"""Stage 4: normalized.jsonl -> capabilities.jsonl (models.PersonCapability per line).

Aggregates contributions into per-person HAS_SKILL / HAS_SPECIALIZATION payloads with
evidence counts, last-used dates, and exponential recency decay.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from ..models import Contribution, PersonCapability
from ..settings import DATA_DIR, settings

NORM_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
CAPS_PATH = DATA_DIR / "contributions" / "capabilities.jsonl"


def period_end(period: str) -> date:
    """'2018-Q3' -> 2018-09-30 (approx: last month of quarter, day 28)."""
    year, q = period.split("-Q")
    return date(int(year), int(q) * 3, 28)


def decay(last_used: date, half_life_days: int, as_of: date | None = None) -> float:
    days = ((as_of or date.today()) - last_used).days
    return math.exp(-math.log(2) * max(days, 0) / half_life_days)


def build_capabilities(contribs: list[Contribution]) -> list[PersonCapability]:
    half_life = settings["projections.recency_half_life_days"]
    acc: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "ids": [], "last": date.min})
    for c in contribs:
        end = period_end(c.period)
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
            last_used=v["last"], decay_score=round(decay(v["last"], half_life), 4),
        )
        for (pid, term, kind), v in acc.items()
    ]


def main() -> None:
    contribs = [Contribution.model_validate_json(l) for l in open(NORM_PATH)]
    caps = build_capabilities(contribs)
    with open(CAPS_PATH, "w") as f:
        for cap in caps:
            f.write(cap.model_dump_json() + "\n")
    print(f"{len(caps)} person-capability edges -> {CAPS_PATH}")


if __name__ == "__main__":
    main()
