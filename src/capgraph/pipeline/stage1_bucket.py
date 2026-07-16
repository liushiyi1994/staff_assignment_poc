"""Stage 1: tickets.parquet -> data/buckets/buckets.jsonl (models.Bucket per line).

Groups tickets per person x project x quarter; deterministically chunks oversized
buckets; drops buckets below min size. Only tickets safely resolved BEFORE
dataset.holdout_cutoff enter buckets — post-cutoff data is reserved for eval.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

import pandas as pd

from ..models import Bucket, Ticket
from ..privacy import LeakageSanitizer, roster_identifiers
from ..settings import DATA_DIR, settings

BUCKETS_PATH = DATA_DIR / "buckets" / "buckets.jsonl"


def quarter_of(ts: pd.Timestamp) -> str:
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def _as_string_list(value) -> list[str]:
    """Normalize Arrow/Pandas list scalars before Pydantic validation."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [str(item) for item in value]


def _project_domain(project_key: str) -> str:
    domains = settings.get("dataset.project_domains", {})
    if not isinstance(domains, Mapping):
        raise TypeError("dataset.project_domains must be a mapping of project key to domain")
    domain = str(domains.get(project_key, "")).strip()
    if not domain:
        raise ValueError(f"missing non-empty dataset.project_domains entry for {project_key}")
    return domain


def validate_profile_evidence(
    tickets: pd.DataFrame,
    *,
    cutoff: str | date | datetime | pd.Timestamp,
) -> None:
    """Validate the hardened Stage 0 rows that can enter a profile.

    Post-boundary rows remain available to the benchmark manifest, which records
    their individual exclusion reasons instead of rejecting the entire dataset.
    """
    required = {
        "source_issue_id",
        "key",
        "project_key",
        "evidence_person_id",
        "evidence_person_name",
        "summary",
        "summary_provenance",
        "description",
        "description_provenance",
        "components",
        "components_provenance",
        "labels",
        "snapshot_resolved_at",
        "resolved_at",
        "resolved_at_provenance",
        "created_at",
        "query_time_source",
        "temporal_exclusion_reason",
    }
    missing = required.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing hardened Stage 0 columns: {sorted(missing)}")
    history = historical_profile_rows(tickets, cutoff=cutoff)
    query_sources = set(history["query_time_source"].dropna().astype(str))
    if history["query_time_source"].isna().any() or query_sources - {"created_at"}:
        raise ValueError(f"unsupported query_time_source values: {sorted(query_sources)}")
    for text_column, provenance_column in (
        ("summary", "summary_provenance"),
        ("description", "description_provenance"),
    ):
        has_text = history[text_column].fillna("").astype(str).str.strip().ne("")
        provenance = history[provenance_column].fillna("").astype(str)
        safe = provenance.eq("snapshot_no_recorded_change") | provenance.str.startswith(
            "change_log_from_"
        )
        if (has_text & ~safe).any():
            raise ValueError(f"unsafe {text_column} provenance in historical evidence")
    has_components = history["components"].map(lambda value: bool(_as_string_list(value)))
    safe_components = (
        history["components_provenance"].fillna("").astype(str)
        == "snapshot_no_recorded_change"
    )
    if (has_components & ~safe_components).any():
        raise ValueError("unsafe component provenance in historical evidence")
    safe_resolution = history["resolved_at_provenance"].fillna("").astype(str).isin(
        {
            "snapshot_no_recorded_resolution_change",
            "resolution_change_log_boundary",
        }
    )
    if (~safe_resolution).any():
        raise ValueError("unsafe resolution-time provenance in historical evidence")


def _evidence_ticket(row: dict, sanitizer: LeakageSanitizer) -> Ticket:
    """Create the historical view without final-snapshot assignment metadata."""
    description = row.get("description")
    if description is not None and bool(pd.isna(description)):
        description = None
    if description is not None:
        description = sanitizer.strip(str(description))
    summary = row.get("summary")
    summary = "" if summary is None or bool(pd.isna(summary)) else str(summary)
    payload = {
        **row,
        "summary": sanitizer.strip(summary),
        "description": description,
        "person_id": None,
        "person_name": None,
        "type": None,
        "resolution": None,
        "snapshot_resolved_at": None,
        "assigned_at": None,
        "temporal_exclusion_reason": None,
        "assignee_provenance": "final_outcome_redacted_from_evidence_view",
        # Component associations are aggregated for Stage 0 audit, but
        # Component.Name is an unversioned final entity snapshot. Redact it
        # from temporal evidence even when the issue association never changed.
        "components": [],
        "components_provenance": "redacted_unversioned_component_name",
        "labels": _as_string_list(row.get("labels")),
    }
    return Ticket(**payload)


def _chunk_sizes(ticket_count: int, *, min_n: int, max_n: int) -> list[int]:
    """Return deterministic, no-loss chunk sizes for a qualifying group.

    Prefer the fewest chunks permitted by ``max_n`` and keep early chunks as
    full as possible, while reserving at least ``min_n`` tickets for every
    later chunk. If both limits cannot be satisfied for a particular count,
    retain the minimum (so every emitted bucket remains useful evidence) and
    distribute tickets evenly to minimize the unavoidable maximum overflow.
    """
    if min_n < 1 or max_n < 1 or min_n > max_n:
        raise ValueError(
            "bucketing sizes must satisfy 1 <= min_tickets_per_bucket "
            "<= max_tickets_per_bucket"
        )
    if ticket_count < min_n:
        return []

    min_chunk_count = (ticket_count + max_n - 1) // max_n
    max_chunk_count = ticket_count // min_n
    if min_chunk_count <= max_chunk_count:
        chunk_count = min_chunk_count
        remaining = ticket_count
        sizes: list[int] = []
        for chunks_left in range(chunk_count, 0, -1):
            size = min(max_n, remaining - min_n * (chunks_left - 1))
            sizes.append(size)
            remaining -= size
        return sizes

    # No partition can satisfy both limits (for example 31 tickets with
    # min_n=20 and max_n=30). Keep all chunks above the evidence minimum and
    # spread the unavoidable overflow as evenly as possible.
    chunk_count = max_chunk_count
    size, larger_chunks = divmod(ticket_count, chunk_count)
    return [size + 1] * larger_chunks + [size] * (chunk_count - larger_chunks)


def _utc_naive_timestamp(value: str | date | datetime | pd.Timestamp) -> pd.Timestamp:
    """Normalize a profile boundary for stable comparisons."""
    boundary = pd.Timestamp(value)
    if boundary.tzinfo is not None:
        boundary = boundary.tz_convert("UTC").tz_localize(None)
    return boundary


def historical_profile_rows(
    tickets: pd.DataFrame,
    *,
    cutoff: str | date | datetime | pd.Timestamp,
    project_key: str | None = None,
) -> pd.DataFrame:
    """Return safe, owned evidence strictly before a profile boundary.

    Stage 0 roster construction, Stage 1 bucketing, and benchmark rosters all
    use this view so a ticket cannot count toward eligibility without also being
    available to the profile builder.
    """
    required = {
        "created_at",
        "resolved_at",
        "project_key",
        "evidence_person_id",
        "temporal_exclusion_reason",
    }
    missing = required.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing profile eligibility columns: {sorted(missing)}")

    boundary = _utc_naive_timestamp(cutoff)
    created_at = pd.to_datetime(tickets["created_at"], errors="coerce", utc=True).dt.tz_localize(
        None
    )
    resolved_at = pd.to_datetime(
        tickets["resolved_at"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    person_ids = tickets["evidence_person_id"].astype("string").str.strip()
    mask = (
        created_at.notna()
        & (created_at < boundary)
        & resolved_at.notna()
        & (resolved_at < boundary)
        & tickets["temporal_exclusion_reason"].isna()
        & person_ids.notna()
        & person_ids.ne("")
    )
    if project_key is not None:
        mask &= tickets["project_key"].astype(str).eq(str(project_key))

    history = tickets.loc[mask].copy()
    history["created_at"] = created_at.loc[mask]
    history["resolved_at"] = resolved_at.loc[mask]
    return history


def _retained_profile_person_ids_from_history(
    history: pd.DataFrame,
    *,
    min_n: int,
    max_n: int,
) -> set[str]:
    """Return owners for whom Stage 1 retains at least one bucket."""
    if history.empty:
        # Still validate the configured size relationship.
        _chunk_sizes(0, min_n=min_n, max_n=max_n)
        return set()

    work = history.copy()
    work["_profile_person_id"] = work["evidence_person_id"].astype(str)
    work["_profile_period"] = work["resolved_at"].map(quarter_of)
    retained: set[str] = set()
    for (person_id, _project_key, _period), group in work.groupby(
        ["_profile_person_id", "project_key", "_profile_period"], sort=True
    ):
        if _chunk_sizes(len(group), min_n=min_n, max_n=max_n):
            retained.add(str(person_id))
    return retained


def retained_profile_person_ids(
    tickets: pd.DataFrame,
    *,
    cutoff: str | date | datetime | pd.Timestamp,
    min_tickets_per_bucket: int,
    max_tickets_per_bucket: int,
    project_key: str | None = None,
) -> set[str]:
    """Return people represented by at least one retained Stage 1 bucket."""
    history = historical_profile_rows(
        tickets, cutoff=cutoff, project_key=project_key
    )
    return _retained_profile_person_ids_from_history(
        history,
        min_n=min_tickets_per_bucket,
        max_n=max_tickets_per_bucket,
    )


def profile_eligible_person_ids(
    tickets: pd.DataFrame,
    *,
    cutoff: str | date | datetime | pd.Timestamp,
    min_resolved_tickets: int,
    min_tickets_per_bucket: int,
    max_tickets_per_bucket: int,
    project_key: str | None = None,
) -> set[str]:
    """Apply both activity and retained-profile eligibility requirements."""
    if min_resolved_tickets < 1:
        raise ValueError("min_resolved_tickets must be at least 1")
    history = historical_profile_rows(
        tickets, cutoff=cutoff, project_key=project_key
    )
    counts = history["evidence_person_id"].astype(str).value_counts()
    active = {
        str(person_id)
        for person_id, count in counts.items()
        if count >= min_resolved_tickets
    }
    retained = _retained_profile_person_ids_from_history(
        history,
        min_n=min_tickets_per_bucket,
        max_n=max_tickets_per_bucket,
    )
    return active & retained


def build_buckets(
    tickets: pd.DataFrame,
    *,
    eligible_person_ids: set[str] | None = None,
) -> list[Bucket]:
    max_n = settings["bucketing.max_tickets_per_bucket"]
    min_n = settings["bucketing.min_tickets_per_bucket"]
    cutoff = pd.Timestamp(settings["dataset.holdout_cutoff"])
    validate_profile_evidence(tickets, cutoff=cutoff)

    df = historical_profile_rows(tickets, cutoff=cutoff)
    # Final-snapshot assignment is audit-only. Historical profile evidence belongs
    # to the assignee reconstructed at resolution time.
    ownership_column = "evidence_person_id"
    ownership_name_column = "evidence_person_name"
    df["bucket_person_id"] = df[ownership_column].astype(str)
    df["bucket_person_name"] = df[ownership_name_column].astype(str)
    if eligible_person_ids is not None:
        retained_ids = _retained_profile_person_ids_from_history(
            df, min_n=min_n, max_n=max_n
        )
        missing_profiles = set(eligible_person_ids) - retained_ids
        if missing_profiles:
            raise ValueError(
                "eligible people without a retained Stage 1 profile bucket: "
                + ", ".join(sorted(missing_profiles))
            )
        df = df[df["bucket_person_id"].isin(eligible_person_ids)].copy()
        identifier_mask = tickets["evidence_person_id"].astype("string").isin(
            eligible_person_ids
        )
        identifier_source = tickets.loc[identifier_mask]
    else:
        identifier_source = tickets
    sanitizer = LeakageSanitizer(roster_identifiers(identifier_source))
    df["period"] = df["resolved_at"].map(quarter_of)
    # MySQL does not guarantee row order, and chunk boundaries are part of the
    # extraction contract. Always stabilize rows before grouping and splitting.
    sort_columns = [
        column
        for column in (
            "bucket_person_id",
            "project_key",
            "period",
            "resolved_at",
            "created_at",
            "source_issue_id",
        )
        if column in df
    ]
    df = df.sort_values(sort_columns, kind="stable", na_position="last")

    buckets: list[Bucket] = []
    for (person_id, project_key, period), group in df.groupby(
        ["bucket_person_id", "project_key", "period"]
    ):
        chunk_sizes = _chunk_sizes(len(group), min_n=min_n, max_n=max_n)
        offset = 0
        for i, chunk_size in enumerate(chunk_sizes):
            g = group.iloc[offset:offset + chunk_size]
            offset += chunk_size
            buckets.append(
                Bucket(
                    bucket_id=f"{person_id}|{project_key}|{period}|{i}",
                    person_id=str(person_id),
                    person_name=str(g["bucket_person_name"].iloc[0]),
                    project_key=str(project_key),
                    project_domain=_project_domain(str(project_key)),
                    period=str(period),
                    tickets=[
                        _evidence_ticket(row, sanitizer)
                        for row in g.to_dict("records")
                    ],
                )
            )
    return buckets


def main() -> None:
    tickets = pd.read_parquet(DATA_DIR / "parquet" / "tickets.parquet")
    people = pd.read_parquet(DATA_DIR / "parquet" / "people.parquet")
    eligible_person_ids = set(people["person_id"].dropna().astype(str))
    expected_person_ids = profile_eligible_person_ids(
        tickets,
        cutoff=settings["dataset.holdout_cutoff"],
        min_resolved_tickets=int(settings["dataset.min_tickets_per_person"]),
        min_tickets_per_bucket=int(settings["bucketing.min_tickets_per_bucket"]),
        max_tickets_per_bucket=int(settings["bucketing.max_tickets_per_bucket"]),
    )
    if eligible_person_ids != expected_person_ids:
        raise ValueError(
            "people.parquet is stale or inconsistent with Stage 1 profile eligibility"
        )
    buckets = build_buckets(tickets, eligible_person_ids=eligible_person_ids)
    BUCKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BUCKETS_PATH, "w") as f:
        for b in buckets:
            f.write(b.model_dump_json() + "\n")
    print(f"Wrote {len(buckets)} buckets -> {BUCKETS_PATH}")


if __name__ == "__main__":
    main()
