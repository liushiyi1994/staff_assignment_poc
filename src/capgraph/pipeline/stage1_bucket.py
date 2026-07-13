"""Stage 1: tickets.parquet -> data/buckets/buckets.jsonl (models.Bucket per line).

Groups tickets per person x project x quarter; splits oversized buckets by component;
drops buckets below min size. Only tickets resolved BEFORE dataset.holdout_cutoff enter
buckets — post-cutoff data is reserved for eval.
"""
from __future__ import annotations

import pandas as pd

from ..models import Bucket, Ticket
from ..settings import DATA_DIR, settings

BUCKETS_PATH = DATA_DIR / "buckets" / "buckets.jsonl"


def quarter_of(ts: pd.Timestamp) -> str:
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def build_buckets(tickets: pd.DataFrame) -> list[Bucket]:
    max_n = settings["bucketing.max_tickets_per_bucket"]
    min_n = settings["bucketing.min_tickets_per_bucket"]
    cutoff = pd.Timestamp(settings["dataset.holdout_cutoff"])

    df = tickets[tickets["resolved_at"].notna() & (tickets["resolved_at"] < cutoff)].copy()
    df["period"] = df["resolved_at"].map(quarter_of)

    buckets: list[Bucket] = []
    for (person_id, project_key, period), group in df.groupby(["person_id", "project_key", "period"]):
        splits = [group]
        if len(group) > max_n:
            # split by primary component, chunk any remaining oversized split
            by_comp = [g for _, g in group.groupby(group["components"].map(
                lambda c: c[0] if isinstance(c, list) and c else "_none"))]
            splits = []
            for g in by_comp:
                splits += [g.iloc[i:i + max_n] for i in range(0, len(g), max_n)]
        for i, g in enumerate(splits):
            if len(g) < min_n:
                continue
            buckets.append(Bucket(
                bucket_id=f"{person_id}|{project_key}|{period}|{i}",
                person_id=str(person_id),
                person_name=g["person_name"].iloc[0],
                project_key=str(project_key),
                period=str(period),
                tickets=[Ticket(**row) for row in g.to_dict("records")],
            ))
    return buckets


def main() -> None:
    tickets = pd.read_parquet(DATA_DIR / "parquet" / "tickets.parquet")
    buckets = build_buckets(tickets)
    BUCKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BUCKETS_PATH, "w") as f:
        for b in buckets:
            f.write(b.model_dump_json() + "\n")
    print(f"Wrote {len(buckets)} buckets -> {BUCKETS_PATH}")


if __name__ == "__main__":
    main()
