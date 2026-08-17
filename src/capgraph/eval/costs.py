"""Per-case spend, read from the cost log the gateway already writes.

``llm.call_json`` appends one line per completed request to ``data/llm_costs.jsonl``.
Rather than duplicating that accounting, the benchmark remembers the file's size
before a case and sums the lines that appeared afterwards. This has two properties
worth keeping:

* the reported eval cost is the *logged* cost, so it reconciles exactly with the
  stage budget the gateway enforces — an eval that under-reported its own spend would
  be the least defensible number in the report;
* retries are counted, because they were paid for.

Lines are filtered by stage, so a smoke run or another stage writing concurrently
cannot be attributed to this one.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..llm import cost_log_path


def spend_by_stage(stages: Sequence[str]) -> list[tuple[str, int, float]]:
    """Reconcile spend directly against data/llm_costs.jsonl, never against a tally.

    A study that spans several stage names — a rewrite stage plus one stage per split —
    has no single stage budget the gateway can enforce for it, so its ceiling has to be
    checked against the ledger itself. Returned in the order asked for.
    """
    path = cost_log_path()
    totals: dict[str, list[float]] = {stage: [0, 0.0] for stage in stages}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                bucket = totals.get(str(record.get("stage")))
                if bucket is not None:
                    bucket[0] += 1
                    bucket[1] += float(record["cost_usd"])
    return [(stage, int(totals[stage][0]), round(totals[stage][1], 4)) for stage in stages]


def spend_by_purpose(stages: Sequence[str]) -> dict[str, tuple[int, float]]:
    """The same ledger split by call type, so a run's cost driver is visible."""
    path = cost_log_path()
    totals: dict[str, list[float]] = {}
    if not path.exists():
        return {}
    wanted = set(stages)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if str(record.get("stage")) not in wanted:
                continue
            bucket = totals.setdefault(str(record.get("purpose") or "unlabelled"), [0, 0.0])
            bucket[0] += 1
            bucket[1] += float(record["cost_usd"])
    return {
        name: (int(count), round(cost, 4)) for name, (count, cost) in sorted(totals.items())
    }


@dataclass(frozen=True)
class Spend:
    """What one case cost, split by the gateway's per-call ``purpose`` label."""

    total: float = 0.0
    n_calls: int = 0
    by_purpose: dict[str, float] = field(default_factory=dict)


class CostMeter:
    """Reads the cost log by byte offset: no re-parsing of the whole file per call."""

    def __init__(self, path: Path | None = None):
        self.path = cost_log_path() if path is None else path
        self._offset = self._size()

    def _size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def mark(self) -> None:
        """Start a new measurement window at the log's current end."""
        self._offset = self._size()

    def spend_since(self, *, stage: str | None = None) -> Spend:
        """Sum the records written since the last :meth:`mark`, and move the mark."""
        if not self.path.exists():
            return Spend()
        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(self._offset)
            lines = handle.readlines()
            self._offset = handle.tell()

        total = 0.0
        n_calls = 0
        by_purpose: dict[str, float] = {}
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if stage is not None and record.get("stage") != stage:
                continue
            cost = float(record.get("cost_usd") or 0.0)
            total += cost
            n_calls += 1
            purpose = str(record.get("purpose") or "unlabelled")
            by_purpose[purpose] = by_purpose.get(purpose, 0.0) + cost
        return Spend(total=total, n_calls=n_calls, by_purpose=by_purpose)
