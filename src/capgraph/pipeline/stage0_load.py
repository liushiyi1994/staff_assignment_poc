"""Stage 0: TAWOS MySQL -> data/parquet/tickets.parquet (+ people.parquet, projects.parquet).

DATASET-DEPENDENT: TAWOS table/column names below are best guesses and MUST be verified
against the real schema first:

    python -m capgraph.pipeline.stage0_load --introspect   # print tables + columns
    python -m capgraph.pipeline.stage0_load --report       # per-project ticket/assignee coverage
    python -m capgraph.pipeline.stage0_load                # export configured slice to parquet

Output contract (downstream stages depend on exactly this):
  tickets.parquet  — one row per ticket matching models.Ticket fields
  people.parquet   — person_id, person_name, project_keys, ticket_count
  projects.parquet — project_key, name, domain (domain filled manually in settings or here)

Acceptance criteria: see docs/implementation-plan.md Task 1.
"""
from __future__ import annotations

import argparse
import re

import pandas as pd
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

from ..settings import DATA_DIR, settings

PARQUET_DIR = DATA_DIR / "parquet"


def get_engine():
    if not settings.mysql_url:
        raise SystemExit("Set MYSQL_URL in .env (see .env.example)")
    return create_engine(settings.mysql_url)


def introspect() -> None:
    eng = get_engine()
    with eng.connect() as conn:
        tables = [r[0] for r in conn.execute(text("SHOW TABLES"))]
        print("Tables:", tables)
        for t in tables:
            cols = conn.execute(text(f"DESCRIBE `{t}`")).fetchall()
            print(f"\n{t}:")
            for c in cols:
                print(f"  {c[0]:<30} {c[1]}")


def report() -> None:
    """Per-project: ticket count, % with assignee, % with non-empty description,
    distinct assignees, date range. Used to choose the project slice in settings.yaml."""
    # TODO(claude-code): implement after --introspect confirms schema.
    # Suggested output: a rich table sorted by assignee-coverage * ticket_count.
    raise NotImplementedError("Run --introspect first, then implement report() against real schema")


def strip_markup(text_: str | None, max_chars: int) -> str | None:
    """Strip Jira wiki markup / HTML, collapse whitespace, truncate."""
    if not text_ or not text_.strip():
        return None
    cleaned = BeautifulSoup(text_, "html.parser").get_text(" ")
    cleaned = re.sub(r"\{[^}]{0,40}\}", " ", cleaned)          # {code}, {noformat}, ...
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars] or None


def is_bot(name: str) -> bool:
    lowered = (name or "").lower()
    return any(p in lowered for p in settings["dataset.bot_patterns"])


def export() -> None:
    """Export the configured project slice to parquet.

    TODO(claude-code): after --introspect, write the real SELECT. Shape needed:
      issue key, project key, assignee id+name, type, summary, description,
      components (aggregate), labels (aggregate), resolution, created/resolved dates.
    Then:
      1. drop rows with no assignee, or bot assignee (is_bot)
      2. strip_markup(description, settings['bucketing.max_description_chars'])
      3. keep people with >= dataset.min_tickets_per_person tickets in-slice
      4. validate a sample of rows against models.Ticket
      5. write the three parquet files listed in the module docstring
    """
    raise NotImplementedError


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--introspect", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.introspect:
        introspect()
    elif args.report:
        report()
    else:
        export()
