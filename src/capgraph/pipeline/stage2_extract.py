"""Stage 2: buckets.jsonl -> data/contributions/raw.jsonl (models.Contribution per line).

One LLM call per bucket via llm.call_json. Checkpointed: already-extracted bucket_ids
are skipped on re-run (--force to redo). Budget-guarded by llm.max_stage_cost_usd.
"""
from __future__ import annotations

import argparse

from tqdm import tqdm

from ..llm import call_json
from ..models import Bucket, Contribution
from ..settings import DATA_DIR, load_prompt, settings

RAW_PATH = DATA_DIR / "contributions" / "raw.jsonl"
BUCKETS_PATH = DATA_DIR / "buckets" / "buckets.jsonl"


def format_tickets(bucket: Bucket) -> str:
    lines = []
    for t in bucket.tickets:
        desc = f" | {t.description}" if t.description else ""
        comps = f" [components: {', '.join(t.components)}]" if t.components else ""
        lines.append(f"- {t.key} ({t.type or 'Task'}, {t.resolution or 'open'}): {t.summary}{comps}{desc}")
    return "\n".join(lines)


def extract_one(bucket: Bucket) -> Contribution:
    prompt = load_prompt(
        "extraction",
        person_name=bucket.person_name,
        project_name=bucket.project_key,
        project_domain=bucket.project_domain,
        period=bucket.period,
        tickets=format_tickets(bucket),
    )
    raw = call_json(prompt, model=settings["llm.extraction_model"], stage="stage2")
    return Contribution(
        contribution_id=bucket.bucket_id,
        person_id=bucket.person_id,
        project_key=bucket.project_key,
        period=bucket.period,
        contribution_summary=raw.get("contribution_summary", ""),
        specializations=raw.get("specializations", []),
        skills=raw.get("skills", []),
        confidence=raw.get("confidence", "low"),
        reason=raw.get("reason", ""),
        evidence_ticket_keys=raw.get("evidence_ticket_keys", []),
        skip=raw.get("skip", False),
        skip_reason=raw.get("skip_reason"),
    )


def main(force: bool = False) -> None:
    buckets = [Bucket.model_validate_json(l) for l in open(BUCKETS_PATH)]
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if RAW_PATH.exists() and not force:
        done = {Contribution.model_validate_json(l).contribution_id for l in open(RAW_PATH)}
    mode = "w" if force else "a"
    with open(RAW_PATH, mode) as f:
        for b in tqdm([b for b in buckets if b.bucket_id not in done], desc="extracting"):
            try:
                f.write(extract_one(b).model_dump_json() + "\n")
                f.flush()
            except Exception as e:  # keep going; log and continue
                print(f"FAILED {b.bucket_id}: {e}")
    print(f"Contributions -> {RAW_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    main(force=ap.parse_args().force)
