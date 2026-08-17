"""Stage 2: buckets.jsonl -> contribution records (models.Contribution per line).

One LLM call per bucket via llm.call_json. Two modes whose outputs and budgets
never mix:

* full run  -> data/contributions/raw.jsonl,       cost stage "stage2"
* pilot run -> data/contributions/pilot_raw.jsonl, cost stage "stage2_pilot"

Both are checkpointed per mode (already-extracted bucket_ids are skipped;
``--force`` redoes them). Every response is validated against the Contribution
contract and every evidence key must belong to the source bucket, so only
validated records are written and a re-run retries whatever failed. The command
exits nonzero when the ``extraction.min_valid_rate`` gate is missed.

``--dry-run`` renders prompts, validates inputs, and reports the projected cost
without instantiating an API client or making a single call.
"""
from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from tqdm import tqdm

from ..llm import (
    PILOT_STAGE_SUFFIX,
    CostControlError,
    call_json,
    enforce_call_cost_ceiling,
    enforce_projected_stage_cost,
    estimate_call_cost_usd,
    resolve_max_tokens,
    stage_budget_usd,
    stage_cost_so_far,
)
from ..models import Bucket, Contribution
from ..settings import DATA_DIR, load_prompt, settings
from .stage2_pilot import (
    BUCKETS_PATH,
    PilotManifestEntry,
    digest_file,
    load_buckets,
    load_pilot_manifest,
)

RAW_PATH = DATA_DIR / "contributions" / "raw.jsonl"
PILOT_RAW_PATH = DATA_DIR / "contributions" / "pilot_raw.jsonl"
FULL_STAGE = "stage2"
PILOT_STAGE = FULL_STAGE + PILOT_STAGE_SUFFIX
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

Status = Literal["extracted", "skipped", "invalid", "failed", "rendered"]


class BucketOutcome(BaseModel):
    """What happened to one bucket in one run."""

    bucket_id: str
    status: Status
    detail: str = ""


class ExtractionSummary(BaseModel):
    """Counts and gate result for one run. Returned so callers can assert on it."""

    mode: Literal["full", "pilot"]
    stage: str
    dry_run: bool
    output_path: str
    selected: int
    already_done: int
    attempted: int
    extracted: int = 0
    skipped: int = 0
    invalid: int = 0
    failed: int = 0
    rendered: int = 0
    estimated_cost_usd: float = 0.0
    budget_usd: float = 0.0
    spent_before_usd: float = 0.0
    min_valid_rate: float = 0.0
    outcomes: list[BucketOutcome] = []
    blockers: list[str] = []

    @property
    def valid(self) -> int:
        """Responses that satisfied the contract, including legitimate skips."""
        return self.extracted + self.skipped

    @property
    def valid_rate(self) -> float | None:
        if self.dry_run or not self.attempted:
            return None
        return self.valid / self.attempted

    @property
    def ok(self) -> bool:
        if self.blockers:
            return False
        rate = self.valid_rate
        return rate is None or rate >= self.min_valid_rate


def format_tickets(bucket: Bucket) -> str:
    lines = []
    for t in bucket.tickets:
        desc = f" | {t.description}" if t.description else ""
        comps = f" [components: {', '.join(t.components)}]" if t.components else ""
        # Stage 1 deliberately redacts mutable final-snapshot type/resolution
        # fields from the historical evidence view.
        lines.append(f"- {t.key}: {t.summary}{comps}{desc}")
    return "\n".join(lines)


def validate_bucket_input(bucket: Bucket) -> None:
    """Reject a bucket that cannot support groundable, attributable evidence."""
    if not bucket.tickets:
        raise ValueError("bucket has no tickets")
    keys = [ticket.key for ticket in bucket.tickets]
    if any(not key.strip() for key in keys):
        raise ValueError("bucket contains a ticket with an empty key")
    if len(set(keys)) != len(keys):
        raise ValueError("bucket contains duplicate ticket keys")
    if not any((ticket.summary or "").strip() for ticket in bucket.tickets):
        raise ValueError("bucket has no ticket text to ground a claim in")
    if not bucket.person_name.strip() or not bucket.project_domain.strip():
        raise ValueError("bucket is missing person_name or project_domain")


def render_prompt(bucket: Bucket) -> str:
    """Render the extraction prompt, refusing any unsubstituted placeholder."""
    prompt = load_prompt(
        "extraction",
        person_name=bucket.person_name,
        project_name=bucket.project_key,
        project_domain=bucket.project_domain,
        period=bucket.period,
        tickets=format_tickets(bucket),
    )
    unresolved = sorted(set(_PLACEHOLDER.findall(prompt)))
    if unresolved:
        raise ValueError(f"unrendered extraction placeholders: {unresolved}")
    return prompt


def validate_evidence_keys(contribution: Contribution, bucket: Bucket) -> None:
    """Every cited key must be a ticket of this bucket — no borrowed evidence."""
    keys = contribution.evidence_ticket_keys
    if not keys:
        raise ValueError("response cites no evidence_ticket_keys")
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate evidence keys: {duplicates}")
    foreign = sorted(set(keys) - {ticket.key for ticket in bucket.tickets})
    if foreign:
        raise ValueError(f"evidence keys outside the source bucket: {foreign}")


def build_contribution(raw: Mapping[str, Any], bucket: Bucket) -> Contribution:
    """Validate one response against the Contribution contract.

    Identity fields come from the bucket, never from the response. A skip must
    admit a reason and must not also claim capabilities; anything else must supply
    every contract field, with evidence drawn from this bucket only.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("response is not a JSON object")
    identity = {
        "contribution_id": bucket.bucket_id,
        "person_id": bucket.person_id,
        "project_key": bucket.project_key,
        "period": bucket.period,
    }
    skip = raw.get("skip", False)
    if not isinstance(skip, bool):
        raise ValueError("skip must be a boolean")
    if skip:
        reason = str(raw.get("skip_reason") or raw.get("reason") or "").strip()
        if not reason:
            raise ValueError("skipped response gives no skip_reason")
        if raw.get("specializations") or raw.get("skills") or raw.get("evidence_ticket_keys"):
            raise ValueError("skipped response also claims capabilities or evidence")
        return Contribution(
            **identity,
            contribution_summary="",
            specializations=[],
            skills=[],
            confidence="low",
            reason=reason,
            evidence_ticket_keys=[],
            skip=True,
            skip_reason=reason,
        )

    contribution = Contribution.model_validate({
        **identity,
        "contribution_summary": raw.get("contribution_summary"),
        "specializations": raw.get("specializations"),
        "skills": raw.get("skills"),
        "confidence": raw.get("confidence"),
        "reason": raw.get("reason"),
        "evidence_ticket_keys": raw.get("evidence_ticket_keys"),
        "skip": False,
        "skip_reason": None,
    })
    if not contribution.contribution_summary.strip():
        raise ValueError("response has an empty contribution_summary")
    if not contribution.specializations:
        raise ValueError("response claims no specializations")
    if not contribution.skills:
        raise ValueError("response claims no skills")
    if not contribution.reason.strip():
        raise ValueError("response gives no confidence reason")
    validate_evidence_keys(contribution, bucket)
    return contribution


def _completed_bucket_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        Contribution.model_validate_json(line).contribution_id
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _pilot_buckets(
    entries: Sequence[PilotManifestEntry], buckets: Sequence[Bucket]
) -> list[Bucket]:
    """Take exactly the manifest's buckets, in manifest order, or fail loudly."""
    by_id = {bucket.bucket_id: bucket for bucket in buckets}
    missing = [entry.bucket_id for entry in entries if entry.bucket_id not in by_id]
    if missing:
        raise ValueError(
            "pilot manifest references bucket ids absent from the bucket file: "
            f"{sorted(missing)} — rebuild the manifest against the current Stage 1 output"
        )
    return [by_id[entry.bucket_id] for entry in entries]


def _bucket_digest_warning(recorded: str, buckets_path: Path) -> str | None:
    """Warn when the pilot slice was chosen from a different Stage 1 output."""
    if not recorded or not buckets_path.is_file():
        return None
    current = digest_file(buckets_path)
    if current == recorded:
        return None
    return (
        f"bucket file digest {current[:12]} differs from the manifest's "
        f"{recorded[:12]}; the pilot slice was chosen from a different Stage 1 output"
    )


def run(
    *,
    pilot: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    buckets_path: Path | None = None,
    output_path: Path | None = None,
) -> ExtractionSummary:
    """Extract contributions for one mode's buckets and report an auditable summary."""
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    if force and limit is not None:
        # --force rewrites the output file, so a limited rerun would silently
        # discard already-paid-for records outside the limit.
        raise ValueError("--force cannot be combined with --limit; rerun without --limit")
    mode: Literal["full", "pilot"] = "pilot" if pilot is not None else "full"
    stage = PILOT_STAGE if pilot is not None else FULL_STAGE
    buckets_path = buckets_path or BUCKETS_PATH
    output = output_path or (PILOT_RAW_PATH if pilot is not None else RAW_PATH)

    buckets = load_buckets(buckets_path)
    warnings: list[str] = []
    if pilot is not None:
        entries = load_pilot_manifest(pilot)
        selected = _pilot_buckets(entries, buckets)
        digest_warning = _bucket_digest_warning(entries[0].buckets_digest, buckets_path)
        if digest_warning:
            warnings.append(digest_warning)
    else:
        selected = list(buckets)

    done = set() if force else _completed_bucket_ids(output)
    pending = [bucket for bucket in selected if bucket.bucket_id not in done]
    already_done = len(selected) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    model = str(settings["llm.extraction_model"])
    max_tokens = resolve_max_tokens()
    summary = ExtractionSummary(
        mode=mode,
        stage=stage,
        dry_run=dry_run,
        output_path=str(output),
        selected=len(selected),
        already_done=already_done,
        attempted=len(pending),
        budget_usd=stage_budget_usd(stage),
        spent_before_usd=stage_cost_so_far(stage),
        min_valid_rate=float(settings["extraction.min_valid_rate"]),
    )

    # Pass 1: validate inputs and project the cost offline. No client, no call.
    renderable: list[Bucket] = []
    for bucket in pending:
        try:
            validate_bucket_input(bucket)
            prompt = render_prompt(bucket)
        except ValueError as error:
            summary.invalid += 1
            summary.outcomes.append(
                BucketOutcome(bucket_id=bucket.bucket_id, status="invalid", detail=str(error))
            )
            continue
        estimate = estimate_call_cost_usd(prompt, model=model, max_tokens=max_tokens)
        try:
            enforce_call_cost_ceiling(estimate, model=model)
        except CostControlError as error:
            summary.blockers.append(f"cost_control:{bucket.bucket_id}: {error}")
        summary.estimated_cost_usd += estimate
        renderable.append(bucket)

    if renderable:
        try:
            enforce_projected_stage_cost(summary.estimated_cost_usd, stage=stage)
        except CostControlError as error:
            summary.blockers.append(f"cost_control:{error}")

    if dry_run:
        summary.rendered = len(renderable)
        if summary.invalid:
            summary.blockers.append(
                f"input_invalid:{summary.invalid} bucket(s) failed input validation"
            )
        _print_summary(summary, warnings)
        return summary
    if summary.blockers:
        # Refuse to start rather than spend up to the ceiling and then stop.
        _print_summary(summary, warnings)
        return summary
    if not renderable:
        # Nothing to call for, so leave any existing output alone — even under
        # --force, which is a redo instruction, not an erase instruction.
        _print_summary(summary, warnings)
        return summary

    # Pass 2: authorized calls, one bucket at a time, appending validated records.
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w" if force else "a") as handle:
        for bucket in tqdm(renderable, desc=f"extracting ({mode})"):
            try:
                raw = call_json(
                    render_prompt(bucket), model=model, stage=stage, max_tokens=max_tokens
                )
            except CostControlError as error:
                # Budget or ceiling refusal: stop the run instead of hammering it.
                summary.blockers.append(f"cost_control:{error}")
                break
            except Exception as error:  # transport/parse failure after retries
                summary.failed += 1
                summary.outcomes.append(
                    BucketOutcome(
                        bucket_id=bucket.bucket_id,
                        status="failed",
                        detail=f"{type(error).__name__}: {error}",
                    )
                )
                continue
            try:
                contribution = build_contribution(raw, bucket)
            except ValueError as error:
                summary.invalid += 1
                summary.outcomes.append(
                    BucketOutcome(
                        bucket_id=bucket.bucket_id, status="invalid", detail=str(error)
                    )
                )
                continue
            handle.write(contribution.model_dump_json() + "\n")
            handle.flush()
            if contribution.skip:
                summary.skipped += 1
                summary.outcomes.append(
                    BucketOutcome(
                        bucket_id=bucket.bucket_id,
                        status="skipped",
                        detail=contribution.skip_reason or "",
                    )
                )
            else:
                summary.extracted += 1
                summary.outcomes.append(
                    BucketOutcome(bucket_id=bucket.bucket_id, status="extracted")
                )

    _print_summary(summary, warnings)
    return summary


def _print_summary(summary: ExtractionSummary, warnings: Iterable[str] = ()) -> None:
    label = f"Stage 2 {summary.mode}{' (dry run)' if summary.dry_run else ''}"
    print(
        f"{label}: {summary.selected} selected, {summary.already_done} already done, "
        f"{summary.attempted} attempted"
    )
    print(
        f"  extracted {summary.extracted}  skipped {summary.skipped}  "
        f"invalid {summary.invalid}  failed {summary.failed}  rendered {summary.rendered}"
    )
    print(
        f"  projected cost ${summary.estimated_cost_usd:.4f}, stage '{summary.stage}' "
        f"budget ${summary.budget_usd:.2f}, already logged ${summary.spent_before_usd:.4f}"
    )
    rate = summary.valid_rate
    if rate is None:
        print(f"  valid rate n/a (gate {summary.min_valid_rate:.2f})")
    else:
        comparator = ">=" if rate >= summary.min_valid_rate else "<"
        print(f"  valid rate {rate:.3f} {comparator} gate {summary.min_valid_rate:.2f}")
    for warning in warnings:
        print(f"  WARNING {warning}")
    for outcome in summary.outcomes:
        if outcome.status in {"invalid", "failed"}:
            print(f"  {outcome.status.upper()} {outcome.bucket_id}: {outcome.detail}")
    for blocker in summary.blockers:
        print(f"  BLOCKED {blocker}")
    if summary.valid:
        print(f"Contributions -> {summary.output_path}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pilot",
        type=Path,
        help="pilot manifest from stage2_pilot; processes only its buckets into "
             f"{PILOT_RAW_PATH.name}",
    )
    ap.add_argument("--limit", type=int, help="process at most N pending buckets")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="render prompts, validate inputs, and project cost with zero API calls",
    )
    ap.add_argument("--force", action="store_true", help="ignore checkpoints and rewrite output")
    args = ap.parse_args(argv)
    summary = run(pilot=args.pilot, limit=args.limit, dry_run=args.dry_run, force=args.force)
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
