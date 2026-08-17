"""Label-noise audit: how much of a miss is the system, and how much is the label.

The bug-triage literature reports that the recorded assignee is not the person who did
the work in roughly a fifth of issues, and that cleaning that label moves reported MRR
by a comparable amount (IssueCourier, arXiv:2505.11205; Tuzun et al., IST 2022). This
module measures how much of that failure mode is present *here*, using only the audit
fields Stage 0 kept for the purpose. It is reporting, never a lever: nothing it computes
is allowed to change a ranking, and the metric it produces is reported beside the
headline rather than in place of it.

Two audit fields matter, both from the Stage 0 ticket contract:

* ``person_id`` — the dump's **final assignee snapshot**, explicitly audit-only and
  redacted from every evidence view. The benchmark's truth is ``evidence_person_id``,
  the assignee reconstructed at the safe resolution boundary, so comparing the two is
  exactly the "was this later reassigned to someone else?" question.
* ``assignee_provenance`` — how that reconstruction was made. Two classes appear on the
  selected manifest: ``change_log_to_at_resolution``, where a recorded assignment event
  places the person on the issue at resolution, and
  ``final_snapshot_no_recorded_assignee_change``, where no assignment event was ever
  recorded and the final snapshot stands in with its timing unknown. The second is a
  weaker label, and the audit reports the metrics separately for each class.

The secondary metric is "truth OR final-snapshot assignee": a case counts as hit when
the ranking reaches either label. It can only ever be equal to or higher than the
headline, and where the two labels agree everywhere it is equal by construction — which
is a result about the dataset, not a missing measurement.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..settings import DATA_DIR
from .holdout import BenchmarkManifestEntry

TICKETS_PATH = DATA_DIR / "parquet" / "tickets.parquet"

CHANGE_LOG_LABEL = "change_log_to_at_resolution"
SNAPSHOT_LABEL = "final_snapshot_no_recorded_assignee_change"


@dataclass(frozen=True)
class LabelAudit:
    """What the audit snapshot says about one case's recorded truth."""

    issue_id: str
    issue_key: str
    project_key: str
    truth_person_ids: tuple[str, ...]
    snapshot_person_id: str | None
    provenance: str
    evidence_class: str

    @property
    def reassigned(self) -> bool:
        """The final snapshot names someone the resolution-boundary truth does not."""
        return (
            self.snapshot_person_id is not None
            and self.snapshot_person_id not in self.truth_person_ids
        )

    @property
    def corroborated(self) -> bool:
        """A recorded assignment event places the truth on the issue at resolution."""
        return self.evidence_class == CHANGE_LOG_LABEL

    def accepted_ids(self, roster: Sequence[str]) -> set[str]:
        """Truth OR the final-snapshot assignee, when that person is on the roster.

        A snapshot assignee outside the frozen roster cannot be ranked by any system
        here, so counting them would make the secondary metric unreachable rather than
        more forgiving.
        """
        accepted = set(self.truth_person_ids)
        if self.snapshot_person_id and self.snapshot_person_id in set(roster):
            accepted.add(self.snapshot_person_id)
        return accepted


def _evidence_class(provenance: str) -> str:
    """The ``evidence=`` half of an assignee-provenance string, or the whole of it."""
    for part in str(provenance).split(";"):
        name, separator, value = part.partition("=")
        if separator and name.strip() == "evidence":
            return value.strip()
    return str(provenance).strip()


def load_label_audit(
    cases: Sequence[BenchmarkManifestEntry], *, path: Path | None = None
) -> dict[str, LabelAudit]:
    """Join the manifest's cases onto the Stage 0 audit snapshot by stable issue id."""
    import pandas as pd

    path = TICKETS_PATH if path is None else path
    if not path.is_file():
        raise FileNotFoundError(
            f"missing Stage 0 ticket snapshot: {path}; the label-noise audit reads the "
            "audit-only assignee fields from it"
        )
    frame = pd.read_parquet(
        path, columns=["source_issue_id", "person_id", "assignee_provenance"]
    )
    frame["source_issue_id"] = frame["source_issue_id"].astype(str)
    snapshot = frame.set_index("source_issue_id")

    audits: dict[str, LabelAudit] = {}
    for case in cases:
        if case.issue_id not in snapshot.index:
            continue
        row = snapshot.loc[case.issue_id]
        person = row["person_id"]
        provenance = "" if pd.isna(row["assignee_provenance"]) else str(row["assignee_provenance"])
        audits[case.issue_id] = LabelAudit(
            issue_id=case.issue_id,
            issue_key=case.issue_key,
            project_key=case.project_key,
            truth_person_ids=tuple(str(value) for value in case.truth_person_ids),
            snapshot_person_id=None if pd.isna(person) else str(person),
            provenance=provenance,
            evidence_class=_evidence_class(provenance),
        )
    return audits


@dataclass(frozen=True)
class AuditSummary:
    """The audit's counts over one split, plus the metrics split by label quality."""

    n: int
    reassigned: int
    corroborated: int
    uncorroborated: int
    missing_audit: int
    by_class: dict[str, dict[str, float]]
    misses_by_class: dict[str, int]


def summarize(
    cases: Sequence[BenchmarkManifestEntry],
    audits: Mapping[str, LabelAudit],
    per_case: Mapping[str, Mapping[str, float]],
) -> AuditSummary:
    """Counts and per-label-class metrics for the cases the frozen run scored.

    ``per_case`` maps issue id to that case's metric values (``hit_at_1``,
    ``hit_at_5``, ``mrr``), which is what the harness already computes per case.
    """
    scored = [case for case in cases if case.issue_id in per_case]
    by_class: dict[str, list[Mapping[str, float]]] = {}
    misses: dict[str, int] = {}
    reassigned = corroborated = uncorroborated = missing = 0
    for case in scored:
        audit = audits.get(case.issue_id)
        if audit is None:
            missing += 1
            label = "unknown"
        else:
            label = CHANGE_LOG_LABEL if audit.corroborated else SNAPSHOT_LABEL
            reassigned += audit.reassigned
            corroborated += audit.corroborated
            uncorroborated += not audit.corroborated
        metrics = per_case[case.issue_id]
        by_class.setdefault(label, []).append(metrics)
        if not metrics.get("hit_at_5"):
            misses[label] = misses.get(label, 0) + 1
    return AuditSummary(
        n=len(scored),
        reassigned=reassigned,
        corroborated=corroborated,
        uncorroborated=uncorroborated,
        missing_audit=missing,
        by_class={
            label: {
                "n": float(len(rows)),
                **{
                    metric: sum(row.get(metric, 0.0) for row in rows) / len(rows)
                    for metric in ("hit_at_1", "hit_at_5", "hit_at_10", "mrr")
                },
            }
            for label, rows in sorted(by_class.items())
        },
        misses_by_class=misses,
    )


__all__ = [
    "CHANGE_LOG_LABEL",
    "SNAPSHOT_LABEL",
    "AuditSummary",
    "LabelAudit",
    "load_label_audit",
    "summarize",
]
