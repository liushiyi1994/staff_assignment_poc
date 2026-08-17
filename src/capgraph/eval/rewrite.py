"""Benchmark v4: rewrite each work package into a natural staffing brief.

    uv run python -m capgraph.eval.rewrite --dry-run   # offline: what it would send
    uv run python -m capgraph.eval.rewrite             # SPENDS under bench4_rewrite

This is the only paid step in building the manifest, and it is paid **once**. Every
rewrite is appended to ``data/eval/v4/rewrites.jsonl`` and frozen into the manifest by
:mod:`capgraph.eval.packages`, so rebuilding the benchmark afterwards is deterministic
and free — which is what makes "the same manifest" a checkable claim rather than a
promise.

The leakage rules are the reason this step is narrow:

* the model sees ``brief_raw`` and nothing else — the creation-time titles and
  descriptions of the issues that were planned into the package **before** its as-of
  time, already stripped of identifiers, mentions, and e-mail addresses. No assignee,
  no comment, no resolution, nothing created after the as-of time, and no truth;
* its answer goes back through the same :class:`LeakageSanitizer` before it is stored,
  and the manifest refuses any rewrite that still trips the guard;
* the input is hashed. A stored rewrite whose ``input_digest`` no longer matches the
  package text it claims to describe is treated as absent, not reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence

from ..llm import CostControlError, call_json, estimate_call_cost_usd
from ..privacy import LeakageSanitizer, roster_identifiers
from ..settings import PROMPTS_DIR, load_prompt, settings
from .costs import spend_by_stage
from .packages import (
    PackageManifestEntry,
    brief_digest,
    build_packages,
    load_package_manifest,
    load_rewrites,
    load_sources,
    rewrites_path,
)

REWRITE_PURPOSE = "brief_rewrite"
PENDING_REASONS = frozenset({"rewrite_pending", "rewrite_too_short",
                             "rewrite_leakage_guard_failed"})


def rewrite_stage() -> str:
    return str(settings["eval.v4.rewrite_stage"])


def rewrite_model() -> str:
    return str(settings["eval.v4.rewrite_model"])


def prompt_name() -> str:
    return str(settings["eval.v4.rewrite_prompt"])


def prompt_digest(name: str | None = None) -> str:
    """Content hash of the rewrite prompt: a revision must change the digest."""
    name = prompt_name() if name is None else name
    return hashlib.sha256((PROMPTS_DIR / f"{name}.md").read_bytes()).hexdigest()[:12]


def project_domain(project_key: str) -> str:
    domains = settings.get("dataset.project_domains", {}) or {}
    return str(domains.get(project_key, "software engineering"))


def render_prompt(entry: PackageManifestEntry) -> str:
    """The rewriter's whole view of the world: pre-as-of package text, nothing else."""
    items = "\n\n".join(
        f"- {block.strip()}" for block in entry.brief_raw.split("\n\n") if block.strip()
    )
    return load_prompt(
        prompt_name(), domain=project_domain(entry.project_key), items=items
    )


def pending(entries: Sequence[PackageManifestEntry]) -> list[PackageManifestEntry]:
    """Packages that cleared every offline guard and still need a usable rewrite.

    Every *candidate* is rewritten, not only the ones that end up selected: selection
    happens after the rewrite guards, so rewriting first is what lets the sample be
    drawn once, from cases that are already known to be usable.
    """
    return [entry for entry in entries if entry.exclusion_reason in PENDING_REASONS]


def projected_cost_usd(entries: Sequence[PackageManifestEntry]) -> float:
    """Worst-case spend for a batch, from the same estimator the gateway enforces."""
    max_tokens = int(settings["eval.v4.rewrite_max_output_tokens"])
    return sum(
        estimate_call_cost_usd(render_prompt(entry), model=rewrite_model(),
                               max_tokens=max_tokens)
        for entry in entries
    )


def enforce_track_ceiling(projected_usd: float) -> None:
    """Refuse a batch that would break the owner's whole-track authorization."""
    stages = [
        str(settings["eval.v4.rewrite_stage"]),
        str(settings["eval.v4.validation_stage"]),
        str(settings["eval.v4.test_stage"]),
    ]
    ceiling = float(settings["eval.v4.max_total_cost_usd"])
    spent = sum(cost for _, _, cost in spend_by_stage(stages))
    if spent + projected_usd > ceiling:
        raise CostControlError(
            f"projected benchmark-v4 spend ${spent + projected_usd:.4f} (logged "
            f"${spent:.4f} + projected ${projected_usd:.4f}) exceeds the "
            f"eval.v4.max_total_cost_usd ceiling of ${ceiling:.2f} — escalate to the "
            "orchestrator before running this"
        )


def rewrite_one(entry: PackageManifestEntry, sanitizer: LeakageSanitizer) -> dict[str, object]:
    """One package -> one checkpoint record. Raises on an unusable answer."""
    response = call_json(
        render_prompt(entry),
        model=rewrite_model(),
        stage=rewrite_stage(),
        max_tokens=int(settings["eval.v4.rewrite_max_output_tokens"]),
        purpose=REWRITE_PURPOSE,
    )
    brief = sanitizer.strip(str(response.get("brief") or ""))
    if not brief:
        raise ValueError(f"{entry.package_key}: rewrite returned no brief")
    if sanitizer.contains(brief):
        raise ValueError(f"{entry.package_key}: rewrite still contains a protected pattern")
    return {
        "package_key": entry.package_key,
        "project_key": entry.project_key,
        "brief": brief,
        "model": rewrite_model(),
        "prompt": prompt_name(),
        "prompt_digest": prompt_digest(),
        "input_digest": brief_digest(entry.brief_raw),
        "rewritten_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def append_rewrite(record: dict[str, object]) -> None:
    path = rewrites_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def run(*, limit: int | None = None, dry_run: bool = False) -> dict[str, int]:
    """Rewrite every package still missing a usable brief, then rebuild the manifest."""
    tickets, people, _sprints, _membership = load_sources()
    sanitizer = LeakageSanitizer(roster_identifiers(people))
    entries = load_package_manifest(splits=None)
    todo = pending(entries)
    done = load_rewrites()
    todo = [entry for entry in todo if entry.package_key not in done][:limit]

    projected = projected_cost_usd(todo)
    print(
        f"benchmark v4 rewrite: {len(todo)} package(s) pending of {len(entries)} "
        f"candidates, model {rewrite_model()}, stage {rewrite_stage()}, "
        f"prompt '{prompt_name()}' ({prompt_digest()}), projected worst case "
        f"${projected:.4f}"
    )
    if dry_run or not todo:
        if todo:
            print("\n--- first prompt ---\n")
            print(render_prompt(todo[0]))
        return {"pending": len(todo), "rewritten": 0, "failed": 0}

    enforce_track_ceiling(projected)
    counts = {"pending": len(todo), "rewritten": 0, "failed": 0}
    for index, entry in enumerate(todo, 1):
        try:
            record = rewrite_one(entry, sanitizer)
        except CostControlError as error:
            print(f"\nstopped at {index}/{len(todo)}: {error}", file=sys.stderr)
            break
        except Exception as error:                      # a failure is a recorded result
            counts["failed"] += 1
            print(f"  [{index}/{len(todo)}] {entry.package_key} FAILED {error!r}")
            continue
        append_rewrite(record)
        counts["rewritten"] += 1
        print(
            f"  [{index}/{len(todo)}] {entry.package_key} "
            f"{entry.brief_issue_count} issues -> {len(record['brief'])} chars"
        )

    build_packages()
    stage = rewrite_stage()
    logged = dict(
        (name, (calls, cost)) for name, calls, cost in spend_by_stage([stage])
    )[stage]
    print(f"\nrewrites: {counts}; stage '{stage}' ledger: {logged[0]} calls, "
          f"${logged[1]:.4f}")
    del tickets                                          # loaded only for the roster
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rewrite v4 work packages into briefs")
    parser.add_argument("--dry-run", action="store_true",
                        help="offline: count pending packages and show one prompt")
    parser.add_argument("--limit", type=int, help="rewrite only the first N packages")
    args = parser.parse_args(argv)
    run(limit=args.limit, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
