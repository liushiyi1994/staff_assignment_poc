"""Deterministic-side sweeps: benchmark v4's noise floor, and the G3a / G6 levers.

    uv run python -m capgraph.eval.sweeps --verify-graph              # offline
    uv run python -m capgraph.eval.sweeps --build-study-graph         # offline, slow
    uv run python -m capgraph.eval.sweeps --noise-floor               # SPENDS
    uv run python -m capgraph.eval.sweeps --replay base               # offline
    uv run python -m capgraph.eval.sweeps --replay g3a_df3            # offline
    uv run python -m capgraph.eval.sweeps --replay g6_strength        # offline
    uv run python -m capgraph.eval.sweeps --gates                     # offline
    uv run python -m capgraph.eval.sweeps --arm g3a_df3               # SPENDS, gated
    uv run python -m capgraph.eval.sweeps --report                    # offline

The rerank-redesign study established where the headroom is: on pinned pools the
re-rank turns a 0.143 Hit@1 pool into 0.393, so what limits the system now is the
deterministic side — retrieval and scoring. This module measures the two wave-1 levers
that attack it, and gives v4 the noise floor it has never had.

**The pinning rule, generalized.** No arm comparison is evidence unless everything
except the lever is held fixed. Every condition here replays the *same checkpointed
intent parses* — ``data/eval/rerank_redesign/pin/validation.jsonl``, read-only — because
intent is brief-level and vocabulary-independent, so it pins cleanly across both levers.
:func:`parses_digest` is recorded in every condition's sidecar and compared in the
report, so "the arms parsed the same briefs" is a checked fact. What a lever may then
move is retrieval and scoring, and where it moves the pools (G3a does; G6 cannot)
:func:`pool_diff_rows` reports the diff case by case — that diff *is* the lever's
retrieval effect, not noise.

**The control that licenses everything else.** The ``base`` condition replays those
parses against the production graph with every flag at its default. Its pools must come
back byte-identical to the pin's. They are not asserted to: :func:`pin_agreement_rows`
measures the agreement and the report prints it, because a replay harness that could not
reproduce the pin would make every pool diff below unreadable.

**Isolation.** G3a needs a graph built on the gated vocabulary. Rather than put the
shared graph into a study state and restore it, the study vocabulary is loaded into a
throwaway second Neo4j container (``eval.sweeps.study_graph``, approved 2026-08-15 and
recorded in the work order). The production graph is never written to by this module:
:func:`verify_production_graph` checks its counts at both ends of the study, and the
only writes anywhere are to ``data/eval/sweeps/``.

**Spend.** Two stage names, one ceiling across both (``eval.sweeps.max_total_cost_usd``),
re-checked before every chunk of cases. The v4 test split is unreachable from here:
:func:`_require_validation` refuses anything but ``validation``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .. import improvements
from ..models import RoleSpec
from ..query.rank import score_candidate
from ..settings import DATA_DIR, settings
from . import rerank_redesign as rr
from .costs import spend_by_purpose, spend_by_stage
from .metrics import candidate_recall, hit_at_k, mrr, query_context, recall_at_k
from .packages import PACKAGE_MANIFEST_VERSION, PackageManifestEntry
from .paired import paired_binary, paired_bootstrap, render_paired
from .run_eval import append_record, config_digest, load_checkpoint
from .run_v4 import METRIC_LABELS, engine_overrides
from .scores import CaseScores, RoleScores
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE

STUDY = "sweeps"
BASE_CONDITION = "base"
G3A_CONDITION = "g3a_df3"
G6_CONDITION = "g6_strength"
# Derived rather than run: the constant-scale control is an arithmetic transform of the
# base condition's own components, so it needs no second retrieval pass and cannot
# differ from base by anything except the scale.
G6_CONTROL = "g6_control"

BINARY_METRICS = ("hit_at_1", "hit_at_5", "hit_at_10")
CONTINUOUS_METRICS = ("recall_at_5", "recall_at_10", "mrr")
POOL_METRICS = ("candidate_recall", "window_hit", "window_recall")

REPORT_PATH = Path(__file__).resolve().parents[3] / "docs" / "deterministic-sweeps-report.md"

# Labels for the two window measures, which are different questions and are routinely
# conflated. Kept apart here so the report cannot blur them.
EXTRA_LABELS = {
    "candidate_recall": "Candidate recall",
    "window_hit": "Window hit rate",
    "window_recall": "Window recall",
}
ALL_LABELS = {**METRIC_LABELS, **EXTRA_LABELS}


class SweepBudgetError(RuntimeError):
    """The study's own ceiling, across both stage names, checked before every chunk."""


class StudyGraphError(RuntimeError):
    """The isolated study graph is missing or not reachable."""


# ---------- configuration ----------

def study(name: str, default=None):
    return settings.get(f"eval.{STUDY}.{name}", default)


def _require_validation() -> str:
    """The only split this module may touch, refused rather than defaulted.

    Same guard as the rerank-redesign study, for the same reason: the v4 test split's
    exposure budget belongs to a later freeze order, and no flag or typo here may reach
    it.
    """
    split = str(study("split", "validation"))
    if split != "validation":
        raise ValueError(
            f"eval.{STUDY}.split is {split!r}; this study is authorized on the v4 "
            "validation split only — the test split's exposure budget belongs to a "
            "separately approved freeze order"
        )
    return split


def engine() -> str:
    return str(study("engine", "v3frozen"))


def brief_variant() -> str:
    return str(study("brief_variant", "rewritten"))


def stage(kind: str) -> str:
    stages = dict(study("stages") or {})
    if kind not in stages:
        raise ValueError(f"eval.{STUDY}.stages has no '{kind}' entry")
    return str(stages[kind])


def stages() -> list[str]:
    """Both cost-log stage names, which share one ceiling."""
    return [str(value) for _, value in sorted(dict(study("stages") or {}).items())]


def root() -> Path:
    return DATA_DIR / "eval" / str(study("root_subdir", STUDY))


def source_pin_path() -> Path:
    return DATA_DIR / "eval" / str(study("source_pin")) / "validation.jsonl"


def baseline_runs_dir() -> Path:
    """The rerank-redesign baseline arm's checkpoint, read-only: the floor's other run."""
    return DATA_DIR / "eval" / str(study("baseline_runs_subdir")) / rr.reference_arm().name


def weights() -> dict[str, float]:
    return dict(settings["scoring.weights"])


def window_width() -> int:
    return int(settings["retrieval.rerank_top_k"])


def cases() -> list[PackageManifestEntry]:
    """The v4 validation manifest entries, in the pin's order."""
    with settings.overridden(engine_overrides(engine())):
        return rr.cases()


@dataclass(frozen=True)
class Condition:
    """One offline condition: a graph, a set of improvement flags, and nothing else."""

    name: str
    label: str
    graph: str                      # "production" | "study"
    flags: dict[str, object]

    @property
    def runs_dir(self) -> Path:
        return root() / "offline" / self.name

    @property
    def checkpoint(self) -> Path:
        return self.runs_dir / f"{_require_validation()}.jsonl"

    @property
    def sidecar(self) -> Path:
        return self.runs_dir / "condition.json"

    @property
    def pin_path(self) -> Path:
        """This condition's own pin: the windows a paid arm would re-rank."""
        return root() / "pins" / self.name / f"{_require_validation()}.jsonl"


def conditions() -> list[Condition]:
    configured = study("conditions") or []
    if not isinstance(configured, Sequence) or isinstance(configured, str):
        raise TypeError(f"eval.{STUDY}.conditions must be a list of condition definitions")
    out = []
    for entry in configured:
        flags = dict(entry.get("flags") or {})
        unknown = sorted(set(flags) - set(improvements.FLAGS))
        if unknown:
            raise KeyError(
                f"condition '{entry['name']}' names non-flag setting(s): {', '.join(unknown)}"
            )
        out.append(
            Condition(
                name=str(entry["name"]),
                label=str(entry["label"]),
                graph=str(entry["graph"]),
                flags=flags,
            )
        )
    return out


def condition_named(name: str) -> Condition:
    for condition in conditions():
        if condition.name == name:
            return condition
    raise ValueError(
        f"unknown condition '{name}'; known: {', '.join(c.name for c in conditions())}"
    )


# ---------- the pinned parses ----------

def pin() -> dict[str, dict]:
    """The rerank-redesign pin, read-only. Its ``intent`` blocks are what every arm reuses."""
    return rr.load_pin(source_pin_path())


def roles_of(record: Mapping) -> list[RoleSpec]:
    """The parsed roles of one pinned case, rebuilt exactly as the pin recorded them."""
    return [RoleSpec.model_validate(role["role"]) for role in record["roles"]]


def parses_digest(records: Mapping[str, dict] | None = None) -> str:
    """Fingerprint of the intent parses an arm replayed.

    The load-bearing invariant of this study: every condition, and the noise floor, and
    any paid arm, must resolve to the *same* roles for the same briefs — the levers are
    allowed to move retrieval and scoring, never what was asked for. Digesting the
    rebuilt :class:`RoleSpec` objects rather than the raw pin bytes means a change in how
    the pin is parsed would show up too.
    """
    pinned = pin() if records is None else records
    payload = json.dumps(
        [
            [issue_id, [role.model_dump(mode="json") for role in roles_of(pinned[issue_id])]]
            for issue_id in sorted(pinned)
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------- graphs ----------

def study_graph(name: str, default=None):
    return study(f"study_graph.{name}", default)


def study_artifacts_dir() -> Path:
    return root() / str(study_graph("artifacts_subdir", "study_artifacts"))


def production_counts_expected() -> dict[str, int]:
    return {str(key): int(value) for key, value in dict(study("production_graph_counts")).items()}


def production_driver():
    from ..query.engine import connected_driver

    return connected_driver()


def study_driver():
    """A driver onto the isolated study graph, or a legible failure."""
    from neo4j import GraphDatabase

    uri = str(study_graph("uri"))
    driver = GraphDatabase.driver(uri, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        driver.verify_connectivity()
    except Exception as error:                       # any driver/service failure
        driver.close()
        raise StudyGraphError(
            f"the isolated study graph is not reachable at {uri} ({error}). Start it "
            f"with `docker run -d --name {study_graph('container')} -p "
            f"{study_graph('bolt_port')}:7687 -p {study_graph('http_port')}:7474 "
            f"-e NEO4J_AUTH=neo4j/... -v {study_graph('volume')}:/data "
            f"{study_graph('image')}`, then `--build-study-graph`."
        ) from error
    return driver


def condition_driver(condition: Condition):
    if condition.graph == "production":
        return production_driver()
    if condition.graph == "study":
        return study_driver()
    raise ValueError(f"condition '{condition.name}' names unknown graph {condition.graph!r}")


def graph_counts(driver) -> dict[str, int]:
    from ..pipeline.stage5_graph import graph_counts as counts

    return counts(driver)


def compare_counts(
    observed: Mapping[str, int], expected: Mapping[str, int]
) -> list[str]:
    """Every expected node/relationship count that the graph does not actually have."""
    return [
        f"{label}: expected {value}, observed {observed.get(label)}"
        for label, value in sorted(expected.items())
        if observed.get(label) != value
    ]


def verify_production_graph() -> dict[str, object]:
    """Read the production graph's counts and compare them to the work order's.

    This study never writes to that graph, so this is a no-change check rather than a
    restoration check — run at both ends so the claim is observed twice, not asserted.
    """
    driver = production_driver()
    try:
        observed = graph_counts(driver)
    finally:
        driver.close()
    expected = production_counts_expected()
    mismatches = compare_counts(observed, expected)
    return {
        "uri": settings.neo4j_uri,
        "observed": observed,
        "expected": expected,
        "mismatches": mismatches,
        "matches": not mismatches,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def record_graph_check(when: str) -> dict[str, object]:
    """Persist one production-graph observation, so the report quotes both ends."""
    result = {"when": when, **verify_production_graph()}
    path = root() / "graph_checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    existing = [row for row in existing if row.get("when") != when] + [result]
    path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def graph_check_rows() -> list[dict]:
    path = root() / "graph_checks.json"
    if not path.exists():
        return []
    # Chronological, not alphabetical: "after" must not print above "before".
    return sorted(
        json.loads(path.read_text(encoding="utf-8")), key=lambda row: row["checked_at"]
    )


def build_study_graph(*, reset: bool = True) -> dict[str, object]:
    """Build the gated vocabulary into the isolated study graph. Offline, $0.

    Stage 3 with the G3a floor on, Stage 4 over its output, Stage 5 into the *other*
    database — through the production loader, with only the input paths and the driver
    changed, so the study graph is built by the same code as the real one. The
    contribution embeddings are deliberately the production cache: Stage 3 rewrites term
    names and never contribution summaries, so the vectors are identical and the vector
    arm is therefore identical across the two vocabularies. Whatever G3a moves, it does
    not move through the vector arm.
    """
    from ..pipeline import stage3_normalize, stage4_project, stage5_graph

    condition = condition_named(G3A_CONDITION)
    floor = int(condition.flags[improvements.FLAG_VOCABULARY])
    target = study_artifacts_dir()
    target.mkdir(parents=True, exist_ok=True)
    norm_path = target / "normalized.jsonl"
    terms_path = target / "terms.jsonl"
    caps_path = target / "capabilities.jsonl"

    print(f"study vocabulary: document-frequency floor {floor} -> {target}")
    with improvements.overridden(condition.flags):
        vocabulary = stage3_normalize.run(
            norm_path=norm_path,
            terms_path=terms_path,
            report_path=target / "terms_report.md",
        )
        stage4_project.run(norm_path=norm_path, caps_path=caps_path)

    driver = study_driver()
    try:
        stage5_graph.apply_schema(driver)
        if reset:
            stage5_graph.reset(driver)
        counts = stage5_graph.load(
            driver,
            contributions_path=norm_path,
            terms_path=terms_path,
            capabilities_path=caps_path,
        )
    finally:
        driver.close()
    summary = {"vocabulary": vocabulary, "counts": counts, "artifacts": str(target)}
    (root() / "study_graph.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def study_graph_summary() -> dict:
    path = root() / "study_graph.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def canonical_counts(path: Path) -> dict[str, int]:
    """Canonical terms per kind in one ``terms.jsonl``, and the aliases folded into them."""
    canonicals: dict[str, int] = {}
    aliases: dict[str, int] = {}
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            kind = str(record["kind"])
            canonicals[kind] = canonicals.get(kind, 0) + 1
            aliases[kind] = aliases.get(kind, 0) + len(record.get("aliases") or [])
    return {
        **{f"{kind}_canonicals": count for kind, count in sorted(canonicals.items())},
        **{f"{kind}_aliases": count for kind, count in sorted(aliases.items())},
    }


def vocabulary_rows() -> list[dict[str, object]]:
    """The production vocabulary beside the gated one, from the two ``terms.jsonl`` files.

    Reported rather than quoted from wave 1: the gate is applied inside the same Stage 3
    run that clusters the terms, so how many canonicals actually survive it on this
    corpus is a property of *this* build, not of a table computed a day earlier.
    """
    from ..pipeline.stage3_normalize import TERMS_PATH

    production = canonical_counts(TERMS_PATH)
    studied = canonical_counts(study_artifacts_dir() / "terms.jsonl")
    return [
        {
            "kind": kind,
            "production": production.get(f"{kind}_canonicals", 0),
            "gated": studied.get(f"{kind}_canonicals", 0),
            "production_aliases": production.get(f"{kind}_aliases", 0),
            "gated_aliases": studied.get(f"{kind}_aliases", 0),
        }
        for kind in ("skill", "specialization")
    ]


# ---------- offline replay ----------

@dataclass(frozen=True)
class RoleReplay:
    """One role's retrieval under one condition: the pool, its components, its order."""

    role: str
    parts: dict[str, dict[str, float]]
    sources: dict[str, list[str]]
    # The engine's own rounded score per candidate, kept as a diagnostic only. Every
    # ordering in this study is recomputed from ``parts`` through the engine's
    # ``combine_parts``, so a transformed arm and the arm it is read against are derived
    # the same way; this column exists to measure what that costs (see
    # :func:`pin_agreement_rows`), not to be ranked on.
    engine_scores: dict[str, float]
    candidate_person_ids: list[str]
    scored_person_ids: list[str]


@dataclass(frozen=True)
class CaseReplay:
    """One benchmark case replayed under one condition."""

    issue_id: str
    issue_key: str
    project_key: str
    truth: frozenset[str]
    roles: tuple[RoleReplay, ...]

    def to_case_scores(self) -> CaseScores:
        """The shared score-checkpoint shape, so ordering and windowing are not re-implemented."""
        return CaseScores(
            issue_id=self.issue_id,
            issue_key=self.issue_key,
            project_key=self.project_key,
            truth=self.truth,
            roles=tuple(
                RoleScores(role=role.role, parts=role.parts, sources=role.sources)
                for role in self.roles
            ),
        )

    def pool(self) -> list[str]:
        """Every person any role retrieved, in the engine's own order, deduplicated."""
        seen: dict[str, None] = {}
        for role in self.roles:
            for person_id in role.candidate_person_ids:
                seen.setdefault(person_id, None)
        return list(seen)

    def to_json(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "issue_key": self.issue_key,
            "project_key": self.project_key,
            "truth_person_ids": sorted(self.truth),
            "roles": [
                {
                    "role": role.role,
                    "parts": role.parts,
                    "sources": role.sources,
                    "engine_scores": role.engine_scores,
                    "candidate_person_ids": role.candidate_person_ids,
                    "scored_person_ids": role.scored_person_ids,
                }
                for role in self.roles
            ],
        }

    @classmethod
    def from_json(cls, record: Mapping) -> CaseReplay:
        return cls(
            issue_id=str(record["issue_id"]),
            issue_key=str(record["issue_key"]),
            project_key=str(record["project_key"]),
            truth=frozenset(str(person) for person in record["truth_person_ids"]),
            roles=tuple(
                RoleReplay(
                    role=str(role["role"]),
                    parts={
                        str(person): {k: float(v) for k, v in parts.items()}
                        for person, parts in role["parts"].items()
                    },
                    sources={
                        str(person): [str(arm) for arm in arms]
                        for person, arms in (role.get("sources") or {}).items()
                    },
                    engine_scores={
                        str(person): float(value)
                        for person, value in (role.get("engine_scores") or {}).items()
                    },
                    candidate_person_ids=[str(p) for p in role["candidate_person_ids"]],
                    scored_person_ids=[str(p) for p in role["scored_person_ids"]],
                )
                for role in record["roles"]
            ),
        )


def replay_condition(condition: Condition, *, limit: int | None = None) -> dict[str, int]:
    """Offline ($0, no model call): replay the pinned parses under one condition.

    Everything downstream of the intent parse is re-run — term resolution, the three
    retrieval arms, expansion and the deterministic score — under this condition's graph
    and flags. Two artifacts come out: the score components (which every offline metric
    below is computed from) and a pin in the rerank-redesign format (the windows a paid
    arm would re-rank, if a gate opens one).
    """
    from ..embeddings import embed
    from ..lexical import default_person_index
    from ..query.engine import retrieve_role

    split = _require_validation()
    pinned = pin()
    manifest = {case.issue_id: case for case in cases()}
    todo = [case for case in cases()[:limit] if case.issue_id in pinned]
    condition.runs_dir.mkdir(parents=True, exist_ok=True)
    condition.pin_path.parent.mkdir(parents=True, exist_ok=True)
    condition.checkpoint.unlink(missing_ok=True)
    condition.pin_path.unlink(missing_ok=True)
    print(
        f"{STUDY} replay '{condition.name}' ({condition.label}): {len(todo)} {split} "
        f"cases, graph {condition.graph}, flags {condition.flags or 'none (defaults)'}"
    )

    counts = {"cases": len(todo), "roles": 0, "candidates": 0}
    with settings.overridden(engine_overrides(engine())), improvements.overridden(
        condition.flags
    ):
        embed(["warm up the local embedding model"])
        lexical_index = (
            default_person_index() if int(settings["retrieval.bm25_top_k"]) > 0 else None
        )
        driver = condition_driver(condition)
        try:
            for index, case in enumerate(todo, 1):
                record = pinned[case.issue_id]
                context = query_context(
                    manifest[case.issue_id], expected_version=PACKAGE_MANIFEST_VERSION
                )
                roles: list[RoleReplay] = []
                pin_roles: list[dict] = []
                for role in roles_of(record):
                    resolution, candidates = retrieve_role(
                        role,
                        record["brief"],
                        driver,
                        roster=context.eligible_roster,
                        as_of=context.as_of_time,
                        lexical_index=lexical_index,
                    )
                    for candidate in candidates:
                        score_candidate(candidate, role, resolution)
                    ordered = sorted(candidates, key=lambda c: (-c.score, c.person_id))
                    roles.append(
                        RoleReplay(
                            role=role.role,
                            parts={c.person_id: dict(c.score_parts) for c in candidates},
                            sources={
                                c.person_id: list(c.retrieval_sources) for c in candidates
                            },
                            engine_scores={c.person_id: c.score for c in candidates},
                            candidate_person_ids=[c.person_id for c in candidates],
                            scored_person_ids=[c.person_id for c in ordered],
                        )
                    )
                    pin_roles.append(rr.pin_role(role, candidates))
                    counts["candidates"] += len(candidates)
                counts["roles"] += len(roles)
                replayed = CaseReplay(
                    issue_id=case.issue_id,
                    issue_key=case.issue_key,
                    project_key=case.project_key,
                    truth=frozenset(str(p) for p in case.truth_person_ids),
                    roles=tuple(roles),
                )
                with condition.checkpoint.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(replayed.to_json(), sort_keys=True) + "\n")
                with condition.pin_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "issue_id": case.issue_id,
                                "issue_key": case.issue_key,
                                "project_key": case.project_key,
                                "split": split,
                                "engine": engine(),
                                "brief_variant": brief_variant(),
                                "manifest_version": PACKAGE_MANIFEST_VERSION,
                                "brief": record["brief"],
                                "intent": record["intent"],
                                "condition": condition.name,
                                "roles": pin_roles,
                                "intent_cost_usd": 0.0,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                print(
                    f"  [{index}/{len(todo)}] {case.issue_key} {case.project_key}: "
                    f"{len(roles)} role(s), pool {len(replayed.pool())}",
                    flush=True,
                )
        finally:
            driver.close()

    condition.sidecar.write_text(
        json.dumps(
            {
                "condition": condition.name,
                "label": condition.label,
                "graph": condition.graph,
                "graph_uri": (
                    settings.neo4j_uri if condition.graph == "production"
                    else str(study_graph("uri"))
                ),
                "flags": condition.flags,
                "engine": engine(),
                "brief_variant": brief_variant(),
                "source_pin": str(source_pin_path()),
                "source_pin_digest": rr.pin_digest(source_pin_path()),
                "parses_digest": parses_digest(),
                "manifest_version": PACKAGE_MANIFEST_VERSION,
                "counts": counts,
                "replayed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return counts


def load_condition(name: str) -> list[CaseReplay]:
    condition = condition_named(name)
    if not condition.checkpoint.exists():
        raise SystemExit(
            f"no replay checkpoint for '{name}' at {condition.checkpoint}; run "
            f"`--replay {name}` first (offline, $0)"
        )
    with condition.checkpoint.open(encoding="utf-8") as handle:
        cases_ = [CaseReplay.from_json(json.loads(line)) for line in handle if line.strip()]
    return sorted(cases_, key=lambda case: case.issue_id)


def condition_sidecar(name: str) -> dict:
    path = condition_named(name).sidecar
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def completed_conditions() -> list[Condition]:
    return [c for c in conditions() if c.checkpoint.exists()]


# ---------- the constant-scale control (G6) ----------

def strength_scales(
    base: Sequence[CaseReplay], variant: Sequence[CaseReplay]
) -> list[float]:
    """Per candidate, how much G6 scaled its ``specialization_match``.

    One number per (case, role, candidate) that actually matched a specialization, so
    the mean below is the average credit the lever hands out on *this* instrument rather
    than a person-level stand-in computed off the corpus.
    """
    by_id = {case.issue_id: case for case in variant}
    ratios: list[float] = []
    for case in base:
        other = by_id.get(case.issue_id)
        if other is None:
            continue
        for role, other_role in zip(case.roles, other.roles, strict=False):
            for person_id, parts in role.parts.items():
                before = float(parts.get("specialization_match", 0.0))
                after = float(other_role.parts.get(person_id, {}).get("specialization_match", 0.0))
                if before > 0:
                    ratios.append(after / before)
    return ratios


def mean_strength_scale(base: Sequence[CaseReplay], variant: Sequence[CaseReplay]) -> float:
    ratios = strength_scales(base, variant)
    return sum(ratios) / len(ratios) if ratios else 1.0


def constant_scale(cases_: Sequence[CaseReplay], scale: float) -> list[CaseReplay]:
    """Wave-1's G6 control: scale every matched specialization by the *same* factor.

    Scaling a component uniformly is close to lowering its weight, and benchmark v2's
    sweep already found ``specialization_match`` wanted less weight. Whatever the
    person-varying arm does beyond this arm is the strength *label* separating people;
    whatever it shares with this arm is a weight rediscovery. Wave-1's table would have
    read as "G6 improves MRR" without this row.
    """
    return [
        replace(
            case,
            roles=tuple(
                replace(
                    role,
                    parts={
                        person_id: {
                            name: (value * scale if name == "specialization_match" else value)
                            for name, value in parts.items()
                        }
                        for person_id, parts in role.parts.items()
                    },
                )
                for role in case.roles
            ),
        )
        for case in cases_
    ]


# ---------- metrics ----------

def per_case_metrics(
    cases_: Sequence[CaseReplay],
    *,
    weights_: Mapping[str, float] | None = None,
    top_k: int | None = None,
) -> dict[str, dict[str, float]]:
    """Deterministic-arm metrics per case, plus the two pool/window measures.

    ``window_hit`` is the share of cases where *any* truth person reaches the re-rank
    window — the ceiling on the full system's Hit@K. ``window_recall`` is the share of a
    case's truth people who reach it. On v1-v3 those were the same number because truth
    was one person; on v4 packages they are not, and conflating them is the easiest way
    to overstate a retrieval lever.
    """
    weights_ = weights() if weights_ is None else dict(weights_)
    top_k = window_width() if top_k is None else top_k
    out: dict[str, dict[str, float]] = {}
    for case in cases_:
        scores = case.to_case_scores()
        ranked = scores.ordering(dict(weights_))
        window = set(scores.window(dict(weights_), top_k))
        truth = set(case.truth)
        out[case.issue_id] = {
            "hit_at_1": hit_at_k(ranked, truth, 1),
            "hit_at_5": hit_at_k(ranked, truth, 5),
            "hit_at_10": hit_at_k(ranked, truth, 10),
            "recall_at_5": recall_at_k(ranked, truth, 5),
            "recall_at_10": recall_at_k(ranked, truth, 10),
            "mrr": mrr(ranked, truth),
            "candidate_recall": candidate_recall(case.pool(), truth),
            "window_hit": float(bool(truth & window)),
            "window_recall": len(truth & window) / len(truth) if truth else 0.0,
        }
    return out


@dataclass(frozen=True)
class ArmSummary:
    """One arm's aggregate row."""

    name: str
    label: str
    n_cases: int
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    candidate_recall: float
    window_hit: float
    window_recall: float
    pool_mean: float
    window_mean: float


def summarize(
    name: str, label: str, cases_: Sequence[CaseReplay], metrics: Mapping[str, Mapping[str, float]]
) -> ArmSummary:
    n = len(metrics) or 1
    top_k = window_width()
    windows = [
        len(case.to_case_scores().window(weights(), top_k)) for case in cases_
    ]
    pools = [len(case.pool()) for case in cases_]

    def mean(metric: str) -> float:
        return sum(values[metric] for values in metrics.values()) / n

    return ArmSummary(
        name=name,
        label=label,
        n_cases=len(metrics),
        hit_at_1=mean("hit_at_1"),
        hit_at_5=mean("hit_at_5"),
        hit_at_10=mean("hit_at_10"),
        recall_at_5=mean("recall_at_5"),
        recall_at_10=mean("recall_at_10"),
        mrr=mean("mrr"),
        candidate_recall=mean("candidate_recall"),
        window_hit=mean("window_hit"),
        window_recall=mean("window_recall"),
        pool_mean=sum(pools) / len(pools) if pools else 0.0,
        window_mean=sum(windows) / len(windows) if windows else 0.0,
    )


def paired_rows(
    baseline: Mapping[str, Mapping[str, float]],
    variant: Mapping[str, Mapping[str, float]],
    *,
    extra: Sequence[str] = POOL_METRICS,
) -> list[str]:
    shared = sorted(set(baseline) & set(variant))
    binary = [
        paired_binary(
            ALL_LABELS[metric],
            {case: baseline[case][metric] for case in shared},
            {case: variant[case][metric] for case in shared},
        )
        for metric in BINARY_METRICS
    ]
    continuous = [
        paired_bootstrap(
            ALL_LABELS[metric],
            {case: baseline[case][metric] for case in shared},
            {case: variant[case][metric] for case in shared},
        )
        for metric in (*CONTINUOUS_METRICS, *extra)
    ]
    return render_paired(binary, continuous)


def delta(
    baseline: Mapping[str, Mapping[str, float]],
    variant: Mapping[str, Mapping[str, float]],
    metric: str,
) -> float:
    """Paired mean difference on one metric over the cases both arms scored."""
    shared = sorted(set(baseline) & set(variant))
    if not shared:
        return 0.0
    return round(
        sum(variant[case][metric] - baseline[case][metric] for case in shared) / len(shared), 4
    )


# ---------- pool, window and pin diffs ----------

def pool_diff_rows(
    before: Sequence[CaseReplay], after: Sequence[CaseReplay]
) -> list[dict[str, object]]:
    """Per case: who the lever added to the pool, who it removed, and whether truth moved."""
    by_id = {case.issue_id: case for case in after}
    rows = []
    for case in before:
        other = by_id.get(case.issue_id)
        if other is None:
            continue
        mine, theirs = set(case.pool()), set(other.pool())
        truth = set(case.truth)
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "project_key": case.project_key,
            "before": len(mine),
            "after": len(theirs),
            "gained": sorted(theirs - mine),
            "lost": sorted(mine - theirs),
            "truth_gained": sorted((theirs - mine) & truth),
            "truth_lost": sorted((mine - theirs) & truth),
            "identical": mine == theirs,
        })
    return rows


def window_diff_rows(
    before: Sequence[CaseReplay], after: Sequence[CaseReplay]
) -> list[dict[str, object]]:
    """Per case: how the re-rank window's *population* changed, ignoring its order.

    The order's G6 gate turns on this: a scoring lever that reshuffles the window but
    shows the model the same 32 people cannot change what a paid arm would see, so it
    cannot earn a paid arm.
    """
    top_k, weights_ = window_width(), weights()
    by_id = {case.issue_id: case for case in after}
    rows = []
    for case in before:
        other = by_id.get(case.issue_id)
        if other is None:
            continue
        mine = set(case.to_case_scores().window(weights_, top_k))
        theirs = set(other.to_case_scores().window(weights_, top_k))
        truth = set(case.truth)
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "before": len(mine),
            "after": len(theirs),
            "entered": sorted(theirs - mine),
            "left": sorted(mine - theirs),
            "truth_entered": sorted((theirs - mine) & truth),
            "truth_left": sorted((mine - theirs) & truth),
            "identical": mine == theirs,
        })
    return rows


def arm_provenance(cases_: Sequence[CaseReplay]) -> dict[str, int]:
    """Which retrieval arm found each candidate, summed over every (case, role) slot.

    The G3a diagnosis rests on this: the study graph shares the production embedding
    cache, so the vector arm cannot move and the lexical arm reads no graph at all. If
    those two columns are identical across conditions and the structured column is not,
    the whole of a vocabulary lever's retrieval effect is the structured arm — which is
    where the mechanism is supposed to be, and is worth showing rather than asserting.
    """
    counts = dict.fromkeys(("vector", "structured", "lexical", "structured_only", "total"), 0)
    for case in cases_:
        for role in case.roles:
            for arms in role.sources.values():
                for arm in ("vector", "structured", "lexical"):
                    counts[arm] += arm in arms
                counts["structured_only"] += arms == ["structured"]
                counts["total"] += 1
    return counts


def pin_agreement_rows(name: str = BASE_CONDITION) -> list[dict[str, object]]:
    """Does the offline replay reproduce the pin it replays? Measured, not assumed.

    Three comparisons per case against ``data/eval/rerank_redesign/pin``: the candidate
    pool in the engine's own order, the deterministic ranking, and the re-rank window's
    population. The pool comparison is the one that matters — if the control condition
    retrieves exactly what the pinned run retrieved, then a pool diff under a lever is
    the lever. The ranking column is expected to be *near* rather than exactly identical
    and the report says why: the engine rounds a candidate's score components to four
    decimals before storing them, and this study deliberately re-derives every arm's
    ordering from those stored components — including the control's — so that a
    transformed arm and its baseline are computed the same way.
    """
    pinned = pin()
    rows = []
    for case in load_condition(name):
        record = pinned.get(case.issue_id)
        if record is None:
            continue
        mine_pool, theirs_pool = case.pool(), rr.pin_pool(record)
        scores = case.to_case_scores()
        engine_order = _round_robin_scored(case)
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "pool_identical": mine_pool == theirs_pool,
            "pool_jaccard": round(
                len(set(mine_pool) & set(theirs_pool)) / len(set(mine_pool) | set(theirs_pool)), 4
            ) if (mine_pool or theirs_pool) else 1.0,
            "engine_order_identical": engine_order == rr.pin_score_ordering(record),
            "recombined_order_identical": (
                scores.ordering(weights()) == rr.pin_score_ordering(record)
            ),
            "window_identical": _pin_window(record) == set(
                scores.window(weights(), window_width())
            ),
        })
    return rows


def _round_robin_scored(case: CaseReplay) -> list[str]:
    from .systems import round_robin

    return round_robin([list(role.scored_person_ids) for role in case.roles])


def _pin_window(record: Mapping) -> set[str]:
    return {
        str(entry["person_id"]) for role in record["roles"] for entry in role["window"]
    }


# ---------- spend control ----------

def study_spend() -> float:
    return sum(cost for _, _, cost in spend_by_stage(stages()))


def ceiling() -> float:
    return float(study("max_total_cost_usd", 8.0))


def enforce_budget(pending_calls: int) -> float:
    """Refuse the next chunk when it would break the owner's authorization."""
    projected = pending_calls * float(dict(study("projection"))["rerank_call_usd"])
    spent = study_spend()
    if spent + projected > ceiling():
        raise SweepBudgetError(
            f"projected {STUDY} spend ${spent + projected:.2f} (logged ${spent:.4f} across "
            f"{', '.join(stages())} + projected ${projected:.2f} for {pending_calls} "
            f"re-rank calls) exceeds the eval.{STUDY}.max_total_cost_usd ceiling of "
            f"${ceiling():.2f} — escalate to the orchestrator before running more"
        )
    return projected


# ---------- paid arms: the noise floor, and any gated tier-2 arm ----------

def paid_runs_dir(name: str) -> Path:
    return root() / "runs" / name


def paid_config(
    name: str, *, pin_path: Path, stage_name: str, flags: Mapping[str, object] | None = None
) -> dict[str, object]:
    """The arm's configuration, built so two arms can never share one checkpoint.

    ``flags`` are the improvement flags the *retrieval* in this pin was produced under.
    They are recorded rather than applied: the re-rank call itself reads none of them,
    but a checkpoint that did not say which vocabulary its pool came from would be
    unreadable a week later.
    """
    arm = rr.reference_arm()
    with settings.overridden({**engine_overrides(engine()), "llm.rerank_prompt": arm.prompt}):
        from .run_v4 import v4_config

        config = v4_config(_require_validation(), engine(), brief_variant())
    config.update({
        "stage": stage_name,
        "study": STUDY,
        "arm": name,
        "rerank_presentation_order": arm.order,
        "pinned_retrieval": True,
        "pin_path": str(pin_path),
        "pin_digest": rr.pin_digest(pin_path),
        "parses_digest": parses_digest(),
        "retrieval_flags": dict(flags or {}),
    })
    return config


def run_paid_arm(
    name: str,
    *,
    pin_path: Path,
    stage_name: str,
    flags: Mapping[str, object] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """SPENDS (re-rank calls only): run the baseline arm over one pinned retrieval.

    The prompt and presentation order are the rerank-redesign *reference arm's*, taken
    from its own configuration rather than restated, because both the noise floor and
    any tier-2 arm have to be that arm and nothing else. What varies between the runs
    this produces is only which pin they replay — the same pin, for the noise floor;
    the lever's own pin, for a tier-2 arm.
    """
    arm = rr.reference_arm()
    split = _require_validation()
    pinned = rr.load_pin(pin_path)
    if not pinned:
        raise rr.PinMismatchError(f"no pin at {pin_path}; nothing to replay")
    empty = [r["issue_id"] for r in pinned.values()
             if not r["roles"] or any(not role["window"] for role in r["roles"])]
    if empty:
        raise rr.PinMismatchError(
            f"{len(empty)} case(s) in {pin_path} have an empty re-rank window "
            f"({', '.join(empty[:5])}); every arm must replay a complete retrieval"
        )

    target = paid_runs_dir(name)
    digest = config_digest(
        paid_config(name, pin_path=pin_path, stage_name=stage_name, flags=flags)
    )
    done = load_checkpoint(split, runs_dir=target)
    stale = sorted({r.get("config_digest", "<none>") for r in done.values()} - {digest})
    if stale:
        raise SystemExit(
            f"arm '{name}' checkpoint holds configuration(s) {', '.join(stale)} but this "
            f"run is {digest}; move {target} aside or restore the settings"
        )

    todo = [
        case for case in cases()[:limit]
        if (CAPGRAPH_FULL, case.issue_id) not in done and case.issue_id in pinned
    ]
    counts = {"cases": len(cases()[:limit]), "skipped": len(cases()[:limit]) - len(todo),
              "ran": 0, "failed": 0}
    calls = sum(len(pinned[case.issue_id]["roles"]) for case in todo)
    print(
        f"{STUDY} arm '{name}': {len(todo)} cases / {calls} re-rank calls, prompt "
        f"'{arm.prompt}', order '{arm.order}', stage '{stage_name}', pin {pin_path.name} "
        f"({rr.pin_digest(pin_path)}), digest {digest}, logged ${study_spend():.4f} of "
        f"${ceiling():.2f}"
    )
    if not todo:
        return counts

    chunk = max(1, int(study("chunk_size", 4)))
    for index, case in enumerate(todo, 1):
        if (index - 1) % chunk == 0:
            pending = sum(
                len(pinned[c.issue_id]["roles"]) for c in todo[index - 1: index - 1 + chunk]
            )
            print(f"  chunk from case {index}: projected ${enforce_budget(pending):.2f}")
        record = pinned[case.issue_id]
        pool = rr.pin_pool(record)
        counts["ran"] += 1
        try:
            output, detail = rr.replay_case(record, arm=arm, stage_name=stage_name)
        except Exception as error:                            # a failure is a result
            counts["failed"] += 1
            append_record(split, _paid_record(name=name, system=CAPGRAPH_FULL, case=case,
                                              digest=digest, pool=pool, ranked=(), cost=0.0,
                                              error=repr(error)), runs_dir=target)
            print(f"  [{index}/{len(todo)}] {case.issue_key} FAILED {error!r}", flush=True)
            continue
        append_record(split, _paid_record(name=name, system=CAPGRAPH_FULL, case=case,
                                          digest=digest, pool=pool,
                                          ranked=output["ranked_ids"],
                                          cost=output["cost_usd"], detail=detail),
                      runs_dir=target)
        append_record(split, _paid_record(name=name, system=CAPGRAPH_SCORE, case=case,
                                          digest=digest, pool=pool,
                                          ranked=rr.pin_score_ordering(record), cost=0.0),
                      runs_dir=target)
        print(
            f"  [{index}/{len(todo)}] {case.issue_key} {case.project_key}: "
            f"{detail['n_ranked_by_rerank']} ranked, {len(detail['rejected'])} rejected, "
            f"spend ${study_spend():.4f}",
            flush=True,
        )
    return counts


def _paid_record(
    *, name: str, system: str, case: PackageManifestEntry, digest: str, pool: Sequence[str],
    ranked: Sequence[str], cost: float, detail: dict | None = None, error: str | None = None,
) -> dict:
    record: dict[str, object] = {
        "split": _require_validation(),
        "system": system,
        "issue_id": case.issue_id,
        "issue_key": case.issue_key,
        "project_key": case.project_key,
        "config_digest": digest,
        "arm": name,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if error is not None:
        record["error"] = error
        return record
    record.update({
        "ranked_ids": list(ranked),
        "candidate_ids": list(pool),
        # Not comparable in a replay: the retrieval this would include was run offline,
        # once, and is not re-run per arm. Recorded as 0.0 rather than as a number a
        # reader could mistake for a measurement of this arm.
        "latency_ms": 0.0,
        "cost_usd": round(float(cost), 6),
    })
    if detail:
        record["detail"] = detail
    return record


def run_noise_floor(*, limit: int | None = None) -> dict[str, int]:
    """SPENDS: repeat the rerank-redesign baseline arm on its own pin, unchanged."""
    return run_paid_arm(
        "noise_floor",
        pin_path=source_pin_path(),
        stage_name=stage("noise_floor"),
        limit=limit,
    )


def paid_per_case(runs_dir: Path, system: str = CAPGRAPH_FULL) -> dict[str, dict[str, float]]:
    """Full-system metrics per case, read back from a paid arm's own checkpoint."""
    by_id = {case.issue_id: case for case in cases()}
    records = load_checkpoint(_require_validation(), runs_dir=runs_dir)
    out: dict[str, dict[str, float]] = {}
    for (name, issue_id), record in records.items():
        if name != system or "error" in record or issue_id not in by_id:
            continue
        truth = set(by_id[issue_id].truth_person_ids)
        ranked = list(record["ranked_ids"])
        pool = list(record.get("candidate_ids") or ())
        out[issue_id] = {
            "hit_at_1": hit_at_k(ranked, truth, 1),
            "hit_at_5": hit_at_k(ranked, truth, 5),
            "hit_at_10": hit_at_k(ranked, truth, 10),
            "recall_at_5": recall_at_k(ranked, truth, 5),
            "recall_at_10": recall_at_k(ranked, truth, 10),
            "mrr": mrr(ranked, truth),
            "candidate_recall": candidate_recall(pool, truth),
        }
    return out


def paid_rankings(runs_dir: Path, system: str = CAPGRAPH_FULL) -> dict[str, list[str]]:
    records = load_checkpoint(_require_validation(), runs_dir=runs_dir)
    return {
        issue_id: list(record["ranked_ids"])
        for (name, issue_id), record in records.items()
        if name == system and "error" not in record
    }


def rejection_row(runs_dir: Path, label: str) -> dict[str, object]:
    """Entries one paid arm's model offered, entries the validator discarded, and the rate.

    The validator in ``query/rank.py`` is untouched by this study, so this is the same
    accounting the rerank-redesign report published — which makes the noise-floor
    repeat's row directly comparable to the arm it repeats, and makes citation
    discipline one more thing the floor is measured on.
    """
    records = [
        record
        for (system, _), record in load_checkpoint(
            _require_validation(), runs_dir=runs_dir
        ).items()
        if system == CAPGRAPH_FULL and "detail" in record
    ]
    accepted = sum(r["detail"]["n_ranked_by_rerank"] for r in records)
    rejected = sum(len(r["detail"].get("rejected", ())) for r in records)
    offered = sum(
        int(r["detail"].get("n_offered_by_rerank")
            or r["detail"]["n_ranked_by_rerank"] + len(r["detail"].get("rejected", ())))
        for r in records
    )
    reasons = sorted({
        problem.split(": ", 1)[-1].split(":")[0]
        for r in records for problem in r["detail"].get("rejected", ())
    })
    return {
        "label": label,
        "cases": len(records),
        "offered": offered,
        "accepted": accepted,
        "rejected": rejected,
        "rate": round(rejected / offered, 4) if offered else 0.0,
        "reasons": reasons,
    }


def paid_cost(runs_dir: Path) -> float:
    records = load_checkpoint(_require_validation(), runs_dir=runs_dir)
    return round(
        sum(
            float(record.get("cost_usd") or 0.0)
            for (name, _), record in records.items()
            if name == CAPGRAPH_FULL and "error" not in record
        ),
        4,
    )


# ---------- the noise floor itself ----------

def agreement_rows() -> list[dict[str, object]]:
    """Per case: how far the repeat run's ranking is from the original's.

    Identical pools, identical prompt, identical presentation order, temperature 0 — so
    every difference in this table is the model answering the same question twice.
    """
    original = paid_rankings(baseline_runs_dir())
    repeat = paid_rankings(paid_runs_dir("noise_floor"))
    rows = []
    for case in cases():
        left, right = original.get(case.issue_id), repeat.get(case.issue_id)
        if left is None or right is None:
            continue
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "project_key": case.project_key,
            "identical": left == right,
            "top1_same": bool(left[:1] == right[:1]),
            "top5_set_same": set(left[:5]) == set(right[:5]),
            "top5_order_same": left[:5] == right[:5],
            "top10_overlap": (
                len(set(left[:10]) & set(right[:10])) / 10 if left and right else 0.0
            ),
        })
    return rows


def noise_floor_measurement() -> dict[str, object]:
    """The measured floor: what one repeat of one unchanged arm moves each metric by."""
    original = paid_per_case(baseline_runs_dir())
    repeat = paid_per_case(paid_runs_dir("noise_floor"))
    shared = sorted(set(original) & set(repeat))
    if not shared:
        return {}
    deltas = {
        metric: delta(original, repeat, metric)
        for metric in (*BINARY_METRICS, *CONTINUOUS_METRICS)
    }
    rows = agreement_rows()
    return {
        "n_cases": len(shared),
        "deltas": deltas,
        "abs_deltas": {metric: abs(value) for metric, value in deltas.items()},
        "hit_at_1_floor": abs(deltas["hit_at_1"]),
        "mrr_floor": abs(deltas["mrr"]),
        "largest_abs_delta": max(abs(value) for value in deltas.values()),
        "identical_rankings": sum(1 for row in rows if row["identical"]),
        "same_top1": sum(1 for row in rows if row["top1_same"]),
        "same_top5_set": sum(1 for row in rows if row["top5_set_same"]),
        "mean_top10_overlap": (
            round(sum(float(row["top10_overlap"]) for row in rows) / len(rows), 4) if rows else 0.0
        ),
        "cost_usd": paid_cost(paid_runs_dir("noise_floor")),
    }


def recorded_claims() -> list[dict[str, object]]:
    """Load-bearing v4 claims already on the record, recomputed against the new floor.

    A floor is only useful if the claims it governs are restated against it, so these
    are recomputed from the rerank-redesign study's own checkpoints (read-only) rather
    than transcribed from its report — a transcription could not notice if it were
    wrong. Each row is a paired delta on the same 28 cases and the same pinned pools.
    """
    from .run_eval import checkpoint_path

    baseline = baseline_runs_dir()
    reversed_arm = baseline.parent / "A"
    rows: list[dict[str, object]] = []
    full = paid_per_case(baseline, CAPGRAPH_FULL)
    score = paid_per_case(baseline, CAPGRAPH_SCORE)
    if full and score:
        rows.append({
            "claim": "the LLM re-rank over the deterministic arm, same pinned pool",
            "source": "rerank-redesign acceptance, finding 2",
            "hit_at_1": delta(score, full, "hit_at_1"),
            "mrr": delta(score, full, "mrr"),
        })
    if checkpoint_path(_require_validation(), runs_dir=reversed_arm).exists():
        rows.append({
            "claim": "presentation order: the same prompt fed worst-first",
            "source": "rerank-redesign acceptance, finding 1 (G7 corrected)",
            "hit_at_1": delta(full, paid_per_case(reversed_arm, CAPGRAPH_FULL), "hit_at_1"),
            "mrr": delta(full, paid_per_case(reversed_arm, CAPGRAPH_FULL), "mrr"),
        })
    return rows


def run_separation_hours() -> float | None:
    """Wall-clock hours between the original arm's last record and the repeat's first.

    Named because it is part of what the floor measures and could otherwise be mistaken
    for pure sampling variance: both runs go through a routed endpoint (OpenRouter), so
    anything that changed provider-side in the gap is inside this number. For the
    question a floor is asked — "would this delta survive being re-run?" — that is the
    right thing to include, but it should be visible rather than implied.
    """
    stamps = []
    for runs in (baseline_runs_dir(), paid_runs_dir("noise_floor")):
        recorded = [
            str(record["recorded_at"])
            for (system, _), record in load_checkpoint(
                _require_validation(), runs_dir=runs
            ).items()
            if system == CAPGRAPH_FULL and record.get("recorded_at")
        ]
        if not recorded:
            return None
        stamps.append((min(recorded), max(recorded)))
    first_end = datetime.fromisoformat(stamps[0][1])
    second_start = datetime.fromisoformat(stamps[1][0])
    return round((second_start - first_end).total_seconds() / 3600, 1)


def measured_floor(metric: str = "hit_at_1") -> float | None:
    """The v4 floor on one metric, or ``None`` when it has not been measured yet.

    Every adoption claim in this study is read against this number rather than against
    the 0.100 the v1 instrument produced, which is what the work order asks for.
    """
    measurement = noise_floor_measurement()
    if not measurement:
        return None
    return float(measurement["abs_deltas"].get(metric, 0.0))


# ---------- gates ----------

@dataclass(frozen=True)
class Gate:
    """One lever's tier-2 gate, decided in code so the report cannot drift from it."""

    lever: str
    passed: bool
    reasons: list[str]
    detail: dict[str, object]


def gate_g3a() -> Gate:
    """Tier 2 runs only if tier 1 shows no recall regression *and* moves something.

    The order's own success test for G3a is the backlog's: "a smaller vocabulary that
    does not improve retrieval is cosmetic". So the gate is not "did anything change" —
    a gated vocabulary always changes term resolution — but whether it changed retrieval
    in a direction worth paying a re-rank arm to explore.
    """
    base = load_condition(BASE_CONDITION)
    variant = load_condition(G3A_CONDITION)
    before, after = per_case_metrics(base), per_case_metrics(variant)
    floor = measured_floor("hit_at_1")
    pool_rows = pool_diff_rows(base, variant)
    window_rows = window_diff_rows(base, variant)
    moved_windows = sum(1 for row in window_rows if not row["identical"])
    detail = {
        "candidate_recall_delta": delta(before, after, "candidate_recall"),
        "window_hit_delta": delta(before, after, "window_hit"),
        "window_recall_delta": delta(before, after, "window_recall"),
        "hit_at_1_delta": delta(before, after, "hit_at_1"),
        "mrr_delta": delta(before, after, "mrr"),
        "cases_with_changed_pool": sum(1 for row in pool_rows if not row["identical"]),
        "cases_with_changed_window": moved_windows,
        "truth_lost_from_pool": sum(len(row["truth_lost"]) for row in pool_rows),
        "truth_gained_in_pool": sum(len(row["truth_gained"]) for row in pool_rows),
        "measured_hit_at_1_floor": floor,
    }
    reasons = []
    no_regression = (
        detail["candidate_recall_delta"] >= 0.0 and detail["window_hit_delta"] >= 0.0
    )
    reasons.append(
        f"the recall guard {'holds' if no_regression else 'FAILS'}: candidate recall "
        f"{detail['candidate_recall_delta']:+.4f} and window hit rate "
        f"{detail['window_hit_delta']:+.4f} — a pool that gains a truth person the "
        "re-rank is never shown is not a recall gain the full system can use"
        if not no_regression
        else f"the recall guard holds: candidate recall "
        f"{detail['candidate_recall_delta']:+.4f}, window hit rate "
        f"{detail['window_hit_delta']:+.4f}"
    )
    improves = floor is not None and detail["hit_at_1_delta"] > floor
    reasons.append(
        f"deterministic arm Hit@1 {detail['hit_at_1_delta']:+.4f} against the measured "
        f"v4 floor of {floor if floor is None else f'{floor:.4f}'} — "
        + ("clears it" if improves else "does not clear it")
    )
    material_window = moved_windows >= max(1, len(window_rows) // 4)
    reasons.append(
        f"{moved_windows}/{len(window_rows)} cases show a changed window population — "
        + ("materially changed" if material_window else "not a material change")
    )
    return Gate(
        lever="G3a",
        passed=bool(no_regression and (improves or material_window)),
        reasons=reasons,
        detail=detail,
    )


def gate_g6() -> Gate:
    """Tier 2 runs only if G6 beats its control beyond the floor *and* moves the window.

    Both halves matter. Wave-1 showed the constant-scale control reproduces almost all
    of the naive gain, so beating the current weights is not evidence for the *label*;
    and a scoring lever that reorders the window without changing who is in it cannot
    change what a paid re-rank arm would be shown, so it cannot earn one.
    """
    base = load_condition(BASE_CONDITION)
    variant = load_condition(G6_CONDITION)
    scale = mean_strength_scale(base, variant)
    control = constant_scale(base, scale)
    metrics = {
        "base": per_case_metrics(base),
        "g6": per_case_metrics(variant),
        "control": per_case_metrics(control),
    }
    floor = measured_floor("hit_at_1")
    mrr_floor = measured_floor("mrr")
    window_rows = window_diff_rows(base, variant)
    moved_windows = sum(1 for row in window_rows if not row["identical"])
    pool_rows = pool_diff_rows(base, variant)
    detail = {
        "mean_strength_scale": round(scale, 4),
        "vs_control_hit_at_1": delta(metrics["control"], metrics["g6"], "hit_at_1"),
        "vs_control_mrr": delta(metrics["control"], metrics["g6"], "mrr"),
        "vs_base_hit_at_1": delta(metrics["base"], metrics["g6"], "hit_at_1"),
        "vs_base_mrr": delta(metrics["base"], metrics["g6"], "mrr"),
        "control_vs_base_mrr": delta(metrics["base"], metrics["control"], "mrr"),
        "cases_with_changed_window": moved_windows,
        "cases_with_changed_pool": sum(1 for row in pool_rows if not row["identical"]),
        "measured_hit_at_1_floor": floor,
        "measured_mrr_floor": mrr_floor,
    }
    beats_control = (
        floor is not None
        and mrr_floor is not None
        and (detail["vs_control_hit_at_1"] > floor or detail["vs_control_mrr"] > mrr_floor)
    )
    truth_moved = sum(
        len(row["truth_entered"]) + len(row["truth_left"]) for row in window_rows
    )
    detail["truth_people_moved_in_or_out_of_window"] = truth_moved
    reasons = [
        f"against the constant-scale control (scale {scale:.4f}): Hit@1 "
        f"{detail['vs_control_hit_at_1']:+.4f}, MRR {detail['vs_control_mrr']:+.4f} — "
        + (
            "clears the measured v4 floor"
            if beats_control
            else "does not beat the control at all; both deltas are negative, and "
            "their magnitudes are inside the measured v4 floor either way"
        ),
        f"{moved_windows}/{len(window_rows)} cases show a changed window population — "
        + (
            (
                "the window moves, but truth-neutrally: no truth person enters or "
                "leaves it"
                if not truth_moved
                else f"the window moves, and {truth_moved} truth-person slot(s) move "
                "with it"
            )
            if moved_windows
            else "nobody new reaches the window"
        ),
    ]
    return Gate(
        lever="G6",
        passed=bool(beats_control and moved_windows),
        reasons=reasons,
        detail=detail,
    )


def gates() -> list[Gate]:
    return [gate_g3a(), gate_g6()]


# ---------- report ----------

def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def arm_table(rows: Sequence[ArmSummary]) -> list[str]:
    lines = [
        "| Arm | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate "
        "recall | Window hit rate | Window recall | Mean pool | Mean window |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.label} | {row.n_cases} | {row.hit_at_1:.3f} | {row.hit_at_5:.3f} | "
            f"{row.hit_at_10:.3f} | {row.recall_at_5:.3f} | {row.recall_at_10:.3f} | "
            f"{row.mrr:.3f} | {row.candidate_recall:.3f} | {row.window_hit:.3f} | "
            f"{row.window_recall:.3f} | {row.pool_mean:.1f} | {row.window_mean:.1f} |"
        )
    return lines


def offline_arms() -> list[tuple[str, str, list[CaseReplay]]]:
    """Every offline arm the report has data for, in reading order."""
    out: list[tuple[str, str, list[CaseReplay]]] = []
    for condition in completed_conditions():
        out.append((condition.name, condition.label, load_condition(condition.name)))
    names = {name for name, _, _ in out}
    if {BASE_CONDITION, G6_CONDITION} <= names:
        base = load_condition(BASE_CONDITION)
        scale = mean_strength_scale(base, load_condition(G6_CONDITION))
        out.append((
            G6_CONTROL,
            f"G6 control: constant scale {scale:.4f} for everyone",
            constant_scale(base, scale),
        ))
    return out


def spend_rows() -> list[str]:
    lines = ["| Stage | Calls | Cost (USD) |", "|---|---:|---:|"]
    total = 0.0
    for name, calls, cost in spend_by_stage(stages()):
        total += cost
        lines.append(f"| `{name}` | {calls} | {cost:.4f} |")
    lines.append(f"| **total** | | **{total:.4f}** |")
    purposes = spend_by_purpose(stages())
    if purposes:
        lines += [
            "",
            "| Call type | Calls | Cost (USD) |",
            "|---|---:|---:|",
            *(f"| `{name}` | {count} | {cost:.4f} |" for name, (count, cost) in purposes.items()),
        ]
    return lines


def render_report() -> str:
    """The study report. Every number here is generated, none transcribed."""
    from .report_sweeps import build_report

    return build_report()


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic-side sweeps (G3a, G6, v4 floor)")
    parser.add_argument("--verify-graph", metavar="WHEN", nargs="?", const="ad-hoc",
                        help="offline: check the production graph's counts and record them")
    parser.add_argument("--build-study-graph", action="store_true",
                        help="offline: build the gated vocabulary into the isolated graph")
    parser.add_argument("--noise-floor", action="store_true",
                        help="SPENDS: repeat the rerank-redesign baseline arm")
    parser.add_argument("--replay", metavar="CONDITION",
                        help="offline: replay the pinned parses under one condition")
    parser.add_argument("--arm", metavar="CONDITION",
                        help="SPENDS: full-system arm on a gated condition's pin")
    parser.add_argument("--gates", action="store_true", help="offline: evaluate both tier-2 gates")
    parser.add_argument("--limit", type=int, help="first N cases only")
    parser.add_argument("--report", action="store_true",
                        help="offline: write docs/deterministic-sweeps-report.md")
    parser.add_argument("--spend", action="store_true", help="offline: logged study spend")
    args = parser.parse_args(argv)
    did = False

    if args.spend:
        did = True
        for name, calls, cost in spend_by_stage(stages()):
            print(f"{name}: {calls} calls, ${cost:.4f}")
        print(f"total: ${study_spend():.4f} of ${ceiling():.2f}")

    if args.verify_graph:
        did = True
        result = record_graph_check(str(args.verify_graph))
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["matches"]:
            return 1

    if args.build_study_graph:
        did = True
        print(json.dumps(build_study_graph(), indent=2, sort_keys=True))

    if args.noise_floor:
        did = True
        print(json.dumps(dict(sorted(run_noise_floor(limit=args.limit).items())), indent=2))

    if args.replay:
        did = True
        counts = replay_condition(condition_named(args.replay), limit=args.limit)
        print(json.dumps(dict(sorted(counts.items())), indent=2))

    if args.arm:
        did = True
        condition = condition_named(args.arm)
        print(json.dumps(dict(sorted(run_paid_arm(
            condition.name,
            pin_path=condition.pin_path,
            stage_name=stage("paid"),
            flags=condition.flags,
            limit=args.limit,
        ).items())), indent=2))

    if args.gates:
        did = True
        for gate in gates():
            print(f"\n{gate.lever}: {'PASS' if gate.passed else 'STOP'}")
            for reason in gate.reasons:
                print(f"  - {reason}")
            print(json.dumps(gate.detail, indent=2, sort_keys=True))

    if args.report:
        did = True
        markdown = render_report()
        REPORT_PATH.write_text(markdown, encoding="utf-8")
        print(f"wrote {REPORT_PATH}")

    if not did:
        parser.error(
            "nothing to do: pass --verify-graph, --build-study-graph, --noise-floor, "
            "--replay, --arm, --gates, --report or --spend"
        )
    return 0


__all__ = [
    "ArmSummary",
    "BASE_CONDITION",
    "CaseReplay",
    "Condition",
    "G3A_CONDITION",
    "G6_CONDITION",
    "G6_CONTROL",
    "Gate",
    "RoleReplay",
    "StudyGraphError",
    "SweepBudgetError",
    "agreement_rows",
    "arm_provenance",
    "arm_table",
    "build_study_graph",
    "canonical_counts",
    "ceiling",
    "condition_named",
    "condition_sidecar",
    "compare_counts",
    "conditions",
    "constant_scale",
    "delta",
    "enforce_budget",
    "gate_g3a",
    "gate_g6",
    "gates",
    "load_condition",
    "mean_strength_scale",
    "measured_floor",
    "noise_floor_measurement",
    "offline_arms",
    "paid_per_case",
    "paid_rankings",
    "paired_rows",
    "parses_digest",
    "per_case_metrics",
    "pin",
    "pin_agreement_rows",
    "pool_diff_rows",
    "record_graph_check",
    "recorded_claims",
    "rejection_row",
    "render_report",
    "replay_condition",
    "roles_of",
    "run_noise_floor",
    "run_separation_hours",
    "run_paid_arm",
    "spend_rows",
    "strength_scales",
    "study_graph_summary",
    "study_spend",
    "summarize",
    "verify_production_graph",
    "vocabulary_rows",
    "window_diff_rows",
]


if __name__ == "__main__":
    raise SystemExit(main())
