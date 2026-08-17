"""Deterministic pilot selection for Stage 2: buckets.jsonl -> pilot manifest.

The pilot is a small, fixed, auditable slice of Stage 1 buckets (default six per
configured project, 30 total) chosen by a versioned seed, so the same buckets and
the same manifest bytes come back on every rebuild. Selection reads bucket
metadata only and never calls a model.

Each manifest line is self-describing: it carries the manifest version, seed,
selection parameters, a digest of the pilot-relevant settings, and a digest of the
source bucket file, so a reviewer can tell exactly which inputs produced it.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models import Bucket
from ..settings import DATA_DIR, settings

BUCKETS_PATH = DATA_DIR / "buckets" / "buckets.jsonl"
PILOT_MANIFEST_PATH = DATA_DIR / "contributions" / "pilot_manifest.v1.jsonl"


class PilotManifestEntry(BaseModel):
    """One deterministically selected pilot bucket with its selection provenance."""

    manifest_version: str
    seed: int
    buckets_per_project: int
    settings_digest: str
    buckets_digest: str = ""
    project_key: str
    selection_rank: int
    bucket_id: str
    person_id: str
    period: str
    ticket_count: int


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_file(path: Path) -> str:
    """SHA-256 of a source file, recorded so a changed input is visible."""
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def pilot_settings_snapshot(
    *,
    manifest_version: str,
    seed: int,
    buckets_per_project: int,
    projects: Sequence[str],
) -> dict[str, Any]:
    """The resolved parameters the pilot depends on, for an auditable digest."""
    return {
        "manifest_version": manifest_version,
        "seed": seed,
        "buckets_per_project": buckets_per_project,
        "projects": list(projects),
        "extraction_model": settings["llm.extraction_model"],
        "temperature": settings["llm.temperature"],
        "max_output_tokens": settings["llm.max_output_tokens"],
    }


def digest_settings(snapshot: Mapping[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return digest_bytes(canonical.encode("utf-8"))


def _stable_score(manifest_version: str, seed: int, project_key: str, bucket_id: str) -> bytes:
    value = f"{manifest_version}\0{seed}\0select\0{project_key}\0{bucket_id}"
    return hashlib.sha256(value.encode("utf-8")).digest()


def _resolved_parameters(
    projects: Sequence[str] | None,
    seed: int | None,
    buckets_per_project: int | None,
    manifest_version: str | None,
) -> tuple[list[str], int, int, str]:
    resolved_projects = [
        str(project) for project in (
            settings["dataset.projects"] if projects is None else projects
        )
    ]
    if not resolved_projects:
        raise ValueError("pilot selection needs at least one configured project")
    if len(set(resolved_projects)) != len(resolved_projects):
        raise ValueError("configured pilot projects contain duplicates")
    resolved_seed = int(
        settings["extraction.pilot.seed"] if seed is None else seed
    )
    resolved_per_project = int(
        settings["extraction.pilot.buckets_per_project"]
        if buckets_per_project is None
        else buckets_per_project
    )
    if resolved_per_project < 1:
        raise ValueError("extraction.pilot.buckets_per_project must be at least 1")
    resolved_version = str(
        settings["extraction.pilot.manifest_version"]
        if manifest_version is None
        else manifest_version
    )
    if not resolved_version:
        raise ValueError("extraction.pilot.manifest_version must be non-empty")
    return resolved_projects, resolved_seed, resolved_per_project, resolved_version


def build_pilot_manifest(
    buckets: Iterable[Bucket],
    *,
    projects: Sequence[str] | None = None,
    seed: int | None = None,
    buckets_per_project: int | None = None,
    manifest_version: str | None = None,
    buckets_digest: str = "",
) -> list[PilotManifestEntry]:
    """Select the pilot buckets deterministically, stratified by project.

    Within a project, buckets are ordered by a seeded SHA-256 score, so the choice
    does not depend on input order or on the shape of the bucket file. Projects are
    emitted in sorted key order for the same reason.
    """
    resolved = _resolved_parameters(projects, seed, buckets_per_project, manifest_version)
    project_keys, resolved_seed, per_project, version = resolved
    snapshot = pilot_settings_snapshot(
        manifest_version=version,
        seed=resolved_seed,
        buckets_per_project=per_project,
        projects=project_keys,
    )
    settings_digest = digest_settings(snapshot)

    configured = set(project_keys)
    by_project: dict[str, list[Bucket]] = defaultdict(list)
    seen_bucket_ids: set[str] = set()
    for bucket in buckets:
        if bucket.bucket_id in seen_bucket_ids:
            raise ValueError(f"duplicate bucket_id in source buckets: {bucket.bucket_id}")
        seen_bucket_ids.add(bucket.bucket_id)
        if bucket.project_key in configured:
            by_project[bucket.project_key].append(bucket)

    entries: list[PilotManifestEntry] = []
    for project_key in sorted(configured):
        candidates = sorted(
            by_project.get(project_key, []),
            key=lambda bucket: _stable_score(
                version, resolved_seed, bucket.project_key, bucket.bucket_id
            ),
        )
        for rank, bucket in enumerate(candidates[:per_project]):
            entries.append(
                PilotManifestEntry(
                    manifest_version=version,
                    seed=resolved_seed,
                    buckets_per_project=per_project,
                    settings_digest=settings_digest,
                    buckets_digest=buckets_digest,
                    project_key=project_key,
                    selection_rank=rank,
                    bucket_id=bucket.bucket_id,
                    person_id=bucket.person_id,
                    period=bucket.period,
                    ticket_count=len(bucket.tickets),
                )
            )
    return entries


def write_pilot_manifest(
    entries: Sequence[PilotManifestEntry],
    *,
    path: Path = PILOT_MANIFEST_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(entry.model_dump_json() + "\n")


def load_pilot_manifest(path: Path = PILOT_MANIFEST_PATH) -> list[PilotManifestEntry]:
    if not path.is_file():
        raise FileNotFoundError(f"missing pilot manifest: {path}")
    entries = [
        PilotManifestEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not entries:
        raise ValueError(f"pilot manifest is empty: {path}")
    bucket_ids = [entry.bucket_id for entry in entries]
    if len(set(bucket_ids)) != len(bucket_ids):
        raise ValueError(f"pilot manifest has duplicate bucket ids: {path}")
    return entries


def load_buckets(path: Path = BUCKETS_PATH) -> list[Bucket]:
    if not path.is_file():
        raise FileNotFoundError(f"missing Stage 1 buckets: {path}; run Stage 1 first")
    return [
        Bucket.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def shortfalls(
    entries: Sequence[PilotManifestEntry], *, projects: Sequence[str], per_project: int
) -> dict[str, int]:
    """Projects that could not supply the requested bucket count. Never silent."""
    selected: dict[str, int] = defaultdict(int)
    for entry in entries:
        selected[entry.project_key] += 1
    return {
        project: selected[project]
        for project in projects
        if selected[project] < per_project
    }


def build_pilot(
    *,
    buckets_path: Path = BUCKETS_PATH,
    manifest_path: Path = PILOT_MANIFEST_PATH,
) -> list[PilotManifestEntry]:
    """Build and persist the pilot manifest from the Stage 1 bucket file."""
    buckets = load_buckets(buckets_path)
    entries = build_pilot_manifest(buckets, buckets_digest=digest_file(buckets_path))
    write_pilot_manifest(entries, path=manifest_path)

    projects, seed, per_project, version = _resolved_parameters(None, None, None, None)
    counts: dict[str, int] = defaultdict(int)
    for entry in entries:
        counts[entry.project_key] += 1
    print(f"Pilot manifest {version} (seed {seed}): {len(entries)} buckets -> {manifest_path}")
    for project in sorted(projects):
        print(f"  {project}: {counts[project]} of {per_project}")
    for project, count in sorted(
        shortfalls(entries, projects=projects, per_project=per_project).items()
    ):
        print(f"  WARNING {project} supplied {count} of {per_project} requested buckets")
    return entries


if __name__ == "__main__":
    build_pilot()
