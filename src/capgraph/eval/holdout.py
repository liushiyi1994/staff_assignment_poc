"""Build eval briefs from post-cutoff tickets (temporal holdout).

Contract:
  build_briefs() -> writes data/eval/briefs.jsonl (models.EvalBrief per line)
  - candidates: tickets resolved AFTER dataset.holdout_cutoff, description length
    >= eval.min_brief_chars, assignee is a known (pre-cutoff) person
  - prefer Epics / larger issues; sample eval.n_briefs stratified by project
  - LEAKAGE GUARD: strip all roster names/usernames from brief text (regex built from
    people.parquet, word-boundary, case-insensitive). Also strip @mentions and emails.
  - ground truth = the ticket's assignee(s)

TODO(claude-code): implement per implementation plan Task 7.
"""
from __future__ import annotations

from ..settings import DATA_DIR

BRIEFS_PATH = DATA_DIR / "eval" / "briefs.jsonl"


def build_briefs() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    build_briefs()
