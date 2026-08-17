"""Fixture tests for the Stage 2 pilot gate. No network, no credentials, no Docker."""
from __future__ import annotations

import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from capgraph import llm
from capgraph.models import Bucket, Contribution, Ticket
from capgraph.pipeline import stage2_extract, stage2_pilot
from capgraph.settings import settings

PROJECTS = ["AAA", "BBB", "CCC"]
PER_PROJECT = 2


def _bucket(project: str, person: int, period: str, chunk: int = 0, tickets: int = 4) -> Bucket:
    return Bucket(
        bucket_id=f"{project}:{person}|{project}|{period}|{chunk}",
        person_id=f"{project}:{person}",
        person_name=f"Person {project}-{person}",
        project_key=project,
        project_domain="distributed systems",
        period=period,
        tickets=[
            Ticket(
                source_issue_id=f"{project}-{person}-{period}-{chunk}-{index}",
                key=f"{project}-{person}{chunk}{index}",
                project_key=project,
                summary=f"Fix broker retry path {index}",
                description="Consumer lag grew after the failover; retries were unbounded.",
                created_at=datetime(2018, 3, 1),
                resolved_at=datetime(2018, 3, 20),
            )
            for index in range(tickets)
        ],
    )


def _buckets(per_project: int = 5) -> list[Bucket]:
    return [
        _bucket(project, person, "2018-Q1")
        for project in PROJECTS
        for person in range(per_project)
    ]


def _write_buckets(path, buckets) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for bucket in buckets:
            handle.write(bucket.model_dump_json() + "\n")


def _manifest(tmp_path, buckets, **kwargs):
    """Write a pilot manifest plus its source bucket file, and return both paths."""
    buckets_path = tmp_path / "buckets" / "buckets.jsonl"
    _write_buckets(buckets_path, buckets)
    manifest_path = tmp_path / "contributions" / "pilot_manifest.v1.jsonl"
    entries = stage2_pilot.build_pilot_manifest(
        buckets,
        projects=PROJECTS,
        buckets_per_project=PER_PROJECT,
        buckets_digest=stage2_pilot.digest_file(buckets_path),
        **kwargs,
    )
    stage2_pilot.write_pilot_manifest(entries, path=manifest_path)
    return buckets_path, manifest_path, entries


@pytest.fixture
def isolated_cost_log(tmp_path, monkeypatch):
    """Never let a developer's real ledger decide whether a test call is affordable."""
    monkeypatch.setattr(llm, "_COST_LOG", tmp_path / "llm_costs.jsonl")


@pytest.fixture
def no_client(monkeypatch):
    """Any attempt to build an API client is a test failure, and is counted."""
    spy = SimpleNamespace(instantiations=0)

    def _forbidden():
        spy.instantiations += 1
        raise AssertionError("no API client may be instantiated in this test")

    monkeypatch.setattr(llm, "_client", _forbidden)
    return spy


class FakeModel:
    """Stand-in for llm.call_json that records prompts and replays canned responses."""

    def __init__(self, responder):
        self._responder = responder
        self.prompts: list[str] = []
        self.stages: list[str] = []

    def __call__(self, prompt, model, stage, max_tokens=None):
        self.prompts.append(prompt)
        self.stages.append(stage)
        result = self._responder(prompt, len(self.prompts) - 1)
        if isinstance(result, Exception):
            raise result
        return result


def _prompt_keys(prompt: str) -> list[str]:
    """Ticket keys as a well-behaved model would read them: inside <tickets> only."""
    block = re.search(r"<tickets>\n(.*?)\n</tickets>", prompt, re.DOTALL)
    assert block, "rendered prompt has no <tickets> block"
    return [line.removeprefix("- ").split(":", 1)[0] for line in block.group(1).splitlines()]


def _valid_response(prompt: str, _index: int = 0) -> dict:
    return {
        "skip": False,
        "contribution_summary": "Hardened the broker retry path and cut consumer lag.",
        "specializations": [{"name": "Distributed systems backend", "strength": "primary"}],
        "skills": [{"name": "Kafka"}, {"name": "retry logic"}],
        "confidence": "high",
        "reason": "Four tickets describe the same retry work.",
        "evidence_ticket_keys": _prompt_keys(prompt)[:3],
    }


def _install(monkeypatch, responder) -> FakeModel:
    fake = FakeModel(responder)
    monkeypatch.setattr(stage2_extract, "call_json", fake)
    return fake


# ---------- pilot selection ----------

def test_pilot_selection_is_deterministic_stratified_and_order_independent():
    buckets = _buckets()
    reversed_buckets = list(reversed(buckets))

    first = stage2_pilot.build_pilot_manifest(
        buckets, projects=PROJECTS, buckets_per_project=PER_PROJECT
    )
    second = stage2_pilot.build_pilot_manifest(
        reversed_buckets, projects=PROJECTS, buckets_per_project=PER_PROJECT
    )

    assert [entry.model_dump() for entry in first] == [entry.model_dump() for entry in second]
    assert len(first) == PER_PROJECT * len(PROJECTS)
    counts = {project: 0 for project in PROJECTS}
    for entry in first:
        counts[entry.project_key] += 1
    assert counts == {project: PER_PROJECT for project in PROJECTS}
    assert len({entry.bucket_id for entry in first}) == len(first)
    assert [entry.selection_rank for entry in first] == [0, 1] * len(PROJECTS)


def test_pilot_manifest_rebuilds_byte_identically(tmp_path):
    buckets = _buckets()
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"

    for path, source in ((first_path, buckets), (second_path, list(reversed(buckets)))):
        stage2_pilot.write_pilot_manifest(
            stage2_pilot.build_pilot_manifest(
                source,
                projects=PROJECTS,
                buckets_per_project=PER_PROJECT,
                buckets_digest="deadbeef",
            ),
            path=path,
        )

    assert first_path.read_bytes() == second_path.read_bytes()


def test_pilot_selection_depends_on_the_configured_seed():
    buckets = _buckets(per_project=8)

    first = stage2_pilot.build_pilot_manifest(
        buckets, projects=PROJECTS, buckets_per_project=PER_PROJECT, seed=1
    )
    second = stage2_pilot.build_pilot_manifest(
        buckets, projects=PROJECTS, buckets_per_project=PER_PROJECT, seed=2
    )

    assert [entry.bucket_id for entry in first] != [entry.bucket_id for entry in second]


def test_manifest_records_selection_parameters_and_digests(tmp_path):
    buckets = _buckets()
    _, manifest_path, entries = _manifest(tmp_path, buckets, seed=4242)

    reloaded = stage2_pilot.load_pilot_manifest(manifest_path)
    assert [entry.model_dump() for entry in reloaded] == [entry.model_dump() for entry in entries]
    digest = stage2_pilot.digest_settings(
        stage2_pilot.pilot_settings_snapshot(
            manifest_version=settings["extraction.pilot.manifest_version"],
            seed=4242,
            buckets_per_project=PER_PROJECT,
            projects=PROJECTS,
        )
    )
    assert {entry.settings_digest for entry in reloaded} == {digest}
    assert {entry.seed for entry in reloaded} == {4242}
    assert {entry.buckets_per_project for entry in reloaded} == {PER_PROJECT}
    assert {len(entry.buckets_digest) for entry in reloaded} == {64}
    assert {entry.ticket_count for entry in reloaded} == {4}


def test_pilot_selection_reports_project_shortfall_instead_of_silently_capping():
    buckets = [_bucket("AAA", 0, "2018-Q1"), _bucket("BBB", 0, "2018-Q1")]

    entries = stage2_pilot.build_pilot_manifest(
        buckets, projects=PROJECTS, buckets_per_project=PER_PROJECT
    )

    assert len(entries) == 2
    assert stage2_pilot.shortfalls(entries, projects=PROJECTS, per_project=PER_PROJECT) == {
        "AAA": 1, "BBB": 1, "CCC": 0
    }


def test_duplicate_bucket_ids_are_rejected():
    duplicate = _bucket("AAA", 0, "2018-Q1")

    with pytest.raises(ValueError, match="duplicate bucket_id"):
        stage2_pilot.build_pilot_manifest(
            [duplicate, duplicate], projects=PROJECTS, buckets_per_project=PER_PROJECT
        )


# ---------- dry run ----------

def test_dry_run_makes_no_client_and_no_call(tmp_path, isolated_cost_log, no_client, monkeypatch):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "contributions" / "pilot_raw.jsonl"

    def _forbidden(*args, **kwargs):
        raise AssertionError("a dry run must not call the model")

    monkeypatch.setattr(stage2_extract, "call_json", _forbidden)

    summary = stage2_extract.run(
        pilot=manifest_path, dry_run=True, buckets_path=buckets_path, output_path=output
    )

    assert no_client.instantiations == 0
    assert not output.exists()
    assert summary.attempted == len(entries) == PER_PROJECT * len(PROJECTS)
    assert summary.rendered == summary.attempted
    assert (summary.extracted, summary.skipped, summary.invalid, summary.failed) == (0, 0, 0, 0)
    assert summary.estimated_cost_usd > 0
    assert summary.stage == "stage2_pilot"
    assert summary.valid_rate is None
    assert summary.ok
    # Projected against the pilot budget, priced by the configured provider's model.
    assert summary.budget_usd == float(settings["llm.pilot_budget_usd"])
    assert summary.estimated_cost_usd < summary.budget_usd
    price_in, price_out = llm.model_price_usd_per_mtok(settings["llm.extraction_model"])
    ceiling = summary.attempted * (
        llm.estimate_tokens("x" * 30_000) / 1e6 * price_in
        + float(settings["llm.max_output_tokens"]) / 1e6 * price_out
    )
    assert 0 < summary.estimated_cost_usd <= ceiling


def test_dry_run_flags_invalid_input_buckets(tmp_path, isolated_cost_log, no_client):
    broken = _bucket("AAA", 0, "2018-Q1")
    broken.tickets[1] = broken.tickets[0]  # duplicate ticket key
    buckets_path, manifest_path, _ = _manifest(tmp_path, [broken, _bucket("BBB", 0, "2018-Q1")])

    summary = stage2_extract.run(
        pilot=manifest_path,
        dry_run=True,
        buckets_path=buckets_path,
        output_path=tmp_path / "pilot_raw.jsonl",
    )

    assert summary.invalid == 1
    assert summary.rendered == 1
    assert any("duplicate ticket keys" in outcome.detail for outcome in summary.outcomes)
    assert not summary.ok


def test_dry_run_projects_cost_and_refuses_an_unaffordable_pilot(
    tmp_path, isolated_cost_log, no_client, monkeypatch
):
    buckets_path, manifest_path, _ = _manifest(tmp_path, _buckets())
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 0.0001)

    summary = stage2_extract.run(
        pilot=manifest_path,
        dry_run=True,
        buckets_path=buckets_path,
        output_path=tmp_path / "pilot_raw.jsonl",
    )

    assert any(blocker.startswith("cost_control:") for blocker in summary.blockers)
    assert not summary.ok
    assert no_client.instantiations == 0


# ---------- checkpointing and limits ----------

def test_checkpoint_skips_completed_buckets_and_force_redoes_them(
    tmp_path, isolated_cost_log, monkeypatch
):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"
    fake = _install(monkeypatch, _valid_response)

    first = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )
    assert (first.attempted, first.extracted, first.already_done) == (len(entries), len(entries), 0)
    assert first.ok

    second = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )
    assert (second.attempted, second.already_done) == (0, len(entries))
    assert len(fake.prompts) == len(entries)
    assert second.ok

    forced = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output, force=True
    )
    assert (forced.attempted, forced.extracted) == (len(entries), len(entries))
    assert len(fake.prompts) == 2 * len(entries)

    written = [
        Contribution.model_validate_json(line)
        for line in output.read_text().splitlines()
        if line.strip()
    ]
    assert len(written) == len(entries)
    assert len({c.contribution_id for c in written}) == len(entries)
    assert {c.contribution_id for c in written} == {entry.bucket_id for entry in entries}
    assert set(fake.stages) == {"stage2_pilot"}


def test_limit_caps_attempted_buckets(tmp_path, isolated_cost_log, monkeypatch):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"
    fake = _install(monkeypatch, _valid_response)

    summary = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output, limit=2
    )

    assert summary.attempted == 2 and summary.selected == len(entries)
    assert len(fake.prompts) == 2
    assert len(output.read_text().splitlines()) == 2
    assert summary.ok


def test_force_with_limit_is_refused_instead_of_discarding_records(tmp_path, isolated_cost_log):
    buckets_path, manifest_path, _ = _manifest(tmp_path, _buckets())

    with pytest.raises(ValueError, match="--force cannot be combined with --limit"):
        stage2_extract.run(
            pilot=manifest_path,
            buckets_path=buckets_path,
            output_path=tmp_path / "pilot_raw.jsonl",
            force=True,
            limit=2,
        )


def test_pilot_and_full_runs_never_share_output_or_checkpoint(
    tmp_path, isolated_cost_log, monkeypatch
):
    buckets = _buckets()
    buckets_path, manifest_path, entries = _manifest(tmp_path, buckets)
    pilot_output = tmp_path / "pilot_raw.jsonl"
    full_output = tmp_path / "raw.jsonl"
    fake = _install(monkeypatch, _valid_response)

    pilot = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=pilot_output
    )
    full = stage2_extract.run(buckets_path=buckets_path, output_path=full_output)

    assert (pilot.mode, pilot.stage) == ("pilot", "stage2_pilot")
    assert (full.mode, full.stage) == ("full", "stage2")
    # The pilot's completed buckets are not treated as done by the full run.
    assert full.attempted == len(buckets) and full.already_done == 0
    assert len(pilot_output.read_text().splitlines()) == len(entries)
    assert len(full_output.read_text().splitlines()) == len(buckets)
    assert fake.stages.count("stage2_pilot") == len(entries)
    assert fake.stages.count("stage2") == len(buckets)


def test_manifest_bucket_missing_from_bucket_file_is_rejected(tmp_path, isolated_cost_log):
    buckets = _buckets()
    _, manifest_path, _ = _manifest(tmp_path, buckets)
    trimmed_path = tmp_path / "trimmed.jsonl"
    _write_buckets(trimmed_path, buckets[:1])

    with pytest.raises(ValueError, match="absent from the bucket file"):
        stage2_extract.run(
            pilot=manifest_path,
            dry_run=True,
            buckets_path=trimmed_path,
            output_path=tmp_path / "pilot_raw.jsonl",
        )


# ---------- response validation ----------

def test_evidence_keys_outside_the_source_bucket_are_invalid_and_unwritten(
    tmp_path, isolated_cost_log, monkeypatch
):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"

    def responder(prompt, index):
        response = _valid_response(prompt)
        if index == 0:
            response["evidence_ticket_keys"] = ["ZZZ-999"]
        return response

    _install(monkeypatch, responder)
    summary = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )

    assert summary.invalid == 1
    assert summary.extracted == len(entries) - 1
    assert len(output.read_text().splitlines()) == len(entries) - 1
    assert any(
        "evidence keys outside the source bucket" in outcome.detail
        for outcome in summary.outcomes
    )
    # 5 of 6 valid is 0.833, so the 0.90 gate also fails the run.
    assert summary.valid_rate == pytest.approx((len(entries) - 1) / len(entries))
    assert not summary.ok


def test_gate_compares_valid_rate_against_the_configured_threshold():
    def _summary(extracted: int, skipped: int = 0) -> stage2_extract.ExtractionSummary:
        return stage2_extract.ExtractionSummary(
            mode="pilot",
            stage="stage2_pilot",
            dry_run=False,
            output_path="pilot_raw.jsonl",
            selected=10,
            already_done=0,
            attempted=10,
            extracted=extracted,
            skipped=skipped,
            invalid=10 - extracted - skipped,
            min_valid_rate=0.9,
        )

    assert _summary(10).ok
    assert _summary(8, skipped=1).ok          # 0.9 exactly clears the gate
    assert not _summary(8).ok                 # 0.8 misses it
    assert _summary(0).attempted == 10 and not _summary(0).ok


def test_threshold_miss_exits_nonzero(tmp_path, isolated_cost_log, monkeypatch):
    buckets_path, manifest_path, _ = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"
    monkeypatch.setattr(stage2_extract, "BUCKETS_PATH", buckets_path)
    monkeypatch.setattr(stage2_extract, "PILOT_RAW_PATH", output)

    def responder(prompt, index):
        response = _valid_response(prompt)
        response["evidence_ticket_keys"] = ["ZZZ-999"]
        return response

    _install(monkeypatch, responder)

    assert stage2_extract.main(["--pilot", str(manifest_path)]) == 1
    assert not output.exists() or output.read_text() == ""


def test_valid_pilot_run_exits_zero(tmp_path, isolated_cost_log, monkeypatch):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"
    monkeypatch.setattr(stage2_extract, "BUCKETS_PATH", buckets_path)
    monkeypatch.setattr(stage2_extract, "PILOT_RAW_PATH", output)
    _install(monkeypatch, _valid_response)

    assert stage2_extract.main(["--pilot", str(manifest_path)]) == 0
    assert len(output.read_text().splitlines()) == len(entries)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"confidence": None}, "confidence"),
        ({"contribution_summary": "  "}, "empty contribution_summary"),
        ({"specializations": []}, "no specializations"),
        ({"skills": []}, "no skills"),
        ({"reason": " "}, "no confidence reason"),
        ({"evidence_ticket_keys": []}, "no evidence_ticket_keys"),
        ({"confidence": "certain"}, "confidence"),
        ({"skip": "yes"}, "skip must be a boolean"),
    ],
)
def test_contract_violations_are_rejected(mutation, message):
    bucket = _bucket("AAA", 0, "2018-Q1")
    response = _valid_response(stage2_extract.render_prompt(bucket))
    response.update(mutation)

    with pytest.raises(ValueError, match=message):
        stage2_extract.build_contribution(response, bucket)


def test_duplicate_evidence_keys_are_rejected():
    bucket = _bucket("AAA", 0, "2018-Q1")
    response = _valid_response(stage2_extract.render_prompt(bucket))
    response["evidence_ticket_keys"] = [bucket.tickets[0].key] * 2

    with pytest.raises(ValueError, match="duplicate evidence keys"):
        stage2_extract.build_contribution(response, bucket)


def test_identity_fields_come_from_the_bucket_not_the_response():
    bucket = _bucket("AAA", 0, "2018-Q1")
    response = _valid_response(stage2_extract.render_prompt(bucket))
    response.update(
        contribution_id="spoofed", person_id="BBB:9", project_key="BBB", period="2030-Q4"
    )

    contribution = stage2_extract.build_contribution(response, bucket)

    assert contribution.contribution_id == bucket.bucket_id
    assert contribution.person_id == bucket.person_id
    assert contribution.project_key == bucket.project_key
    assert contribution.period == bucket.period


def test_skip_requires_a_reason_and_no_capability_claims(
    tmp_path, isolated_cost_log, monkeypatch
):
    bucket = _bucket("AAA", 0, "2018-Q1")
    prompt = stage2_extract.render_prompt(bucket)

    skipped = stage2_extract.build_contribution(
        {"skip": True, "skip_reason": "tickets are one-line chores"}, bucket
    )
    assert skipped.skip and skipped.skip_reason == "tickets are one-line chores"
    assert skipped.specializations == [] and skipped.evidence_ticket_keys == []

    with pytest.raises(ValueError, match="no skip_reason"):
        stage2_extract.build_contribution({"skip": True}, bucket)
    with pytest.raises(ValueError, match="also claims capabilities"):
        stage2_extract.build_contribution(
            {"skip": True, "skip_reason": "vague", "skills": [{"name": "Kafka"}]}, bucket
        )
    assert prompt  # the prompt renders without leftover placeholders


def test_valid_skips_count_toward_the_gate_and_are_written(
    tmp_path, isolated_cost_log, monkeypatch
):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"

    def responder(prompt, index):
        if index % 2:
            return {"skip": True, "skip_reason": "tickets are one-line chores"}
        return _valid_response(prompt)

    _install(monkeypatch, responder)
    summary = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )

    assert summary.skipped == len(entries) // 2
    assert summary.extracted == len(entries) - summary.skipped
    assert summary.valid_rate == 1.0 and summary.ok
    written = [json.loads(line) for line in output.read_text().splitlines()]
    assert sum(record["skip"] for record in written) == summary.skipped


# ---------- failure handling ----------

def test_transport_failures_are_counted_not_swallowed(tmp_path, isolated_cost_log, monkeypatch):
    buckets_path, manifest_path, entries = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"

    def responder(prompt, index):
        if index < 3:
            return RuntimeError("connection reset")
        return _valid_response(prompt)

    _install(monkeypatch, responder)
    summary = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )

    assert summary.failed == 3
    assert summary.extracted == len(entries) - 3
    assert not summary.ok  # 3/6 valid misses the 0.90 gate
    assert any(outcome.status == "failed" for outcome in summary.outcomes)
    # A retry picks up exactly the failed buckets.
    retry = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )
    assert retry.attempted == 3


def test_budget_refusal_stops_the_run_and_blocks_acceptance(
    tmp_path, isolated_cost_log, monkeypatch
):
    buckets_path, manifest_path, _ = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"

    def responder(prompt, index):
        if index == 2:
            return llm.BudgetExceededError("stage 'stage2_pilot' would spend too much")
        return _valid_response(prompt)

    fake = _install(monkeypatch, responder)
    summary = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )

    assert len(fake.prompts) == 3  # stopped at the refusal instead of continuing
    assert summary.extracted == 2
    assert any(blocker.startswith("cost_control:") for blocker in summary.blockers)
    assert not summary.ok


def test_live_run_refuses_to_start_when_projected_cost_exceeds_budget(
    tmp_path, isolated_cost_log, no_client, monkeypatch
):
    buckets_path, manifest_path, _ = _manifest(tmp_path, _buckets())
    output = tmp_path / "pilot_raw.jsonl"
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 0.0001)

    def _forbidden(*args, **kwargs):
        raise AssertionError("an over-budget run must not make a call")

    monkeypatch.setattr(stage2_extract, "call_json", _forbidden)
    summary = stage2_extract.run(
        pilot=manifest_path, buckets_path=buckets_path, output_path=output
    )

    assert not summary.ok
    assert not output.exists()
    assert no_client.instantiations == 0
