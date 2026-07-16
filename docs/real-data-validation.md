# Real-data foundation acceptance

Status: **passed** on 2026-07-16. The validated code commit is
`437ebfcc364dc1f4665fa3423e1b7e297094f285`; documentation is added in the
following commit. This run covered the restored source, Stage 0, Stage 1, and the
benchmark manifest only. It did not run graph loading, retrieval, stages 2–5, or
any LLM/API call.

## Reproducibility envelope

| Item | Validated value |
|---|---|
| Run completed | `2026-07-16T17:41:07Z` |
| Settings SHA-256 | `5b7ed36dc9127061ace2e9068989f67770c7fc7d697a63ac813ff0e0994905a0` |
| Lockfile SHA-256 | `51e19ce124f05c5230893f8af7a502a35e738c6aaf36baa933b0add8ae4574dc` |
| Python / uv | 3.14.6 / 0.11.29 |
| pandas / PyArrow | 3.0.3 / 25.0.0 |
| SQLAlchemy / PyMySQL | 2.0.51 / 2.2.8 |
| MySQL | 8.4.10 |
| MySQL image | `mysql@sha256:c831a0f11348d402b43d77453e17d770be2eef356615a2823fe0f5a0d6c8b9af` |
| Docker client / server / Compose | 29.6.1 / 29.5.2 / 5.3.1 |
| Colima | 0.10.3, macOS virtualization, ARM64 |

The local archive remained ignored and unchanged at 637,550,449 bytes. Its MD5
was `e9c5ecc7649d55f0cf2fb4efb5664494`, its SHA-256 was
`278984f788008c58d338e1f4aa195eae8e5b15b4153e51c247659ef8465917f7`,
and ZIP integrity validation passed. The 4.31 GB SQL member was streamed directly
into MySQL and was not extracted or duplicated.

## Restore and report reconciliation

The restored database contained exactly 458,232 issues, 39 projects, and 12
repositories. Stage 0 schema introspection matched the official TAWOS v1.1
tables and columns used by the pipeline.

The MySQL report matched the canonical archive-stream report exactly for issue,
resolved, assigned, pre/post-cutoff, and threshold-person counts. Its rough
brief estimate is intentionally an upper bound over raw SQL text length, while
the canonical report applies its streaming text rules; neither is the final
manifest count.

| Project | Issues | Pre-cutoff | Post-cutoff | Threshold people | Canonical upper bound | MySQL raw-text bound |
|---|---:|---:|---:|---:|---:|---:|
| MESOS | 10,157 | 9,470 | 687 | 67 | 173 | 181 |
| FAB | 13,682 | 9,393 | 4,289 | 62 | 720 | 731 |
| TIMOB | 22,059 | 20,931 | 1,128 | 61 | 381 | 395 |
| DM | 26,506 | 16,613 | 9,893 | 105 | 1,855 | 1,944 |
| EVG | 10,299 | 6,147 | 4,152 | 21 | 465 | 474 |
| **Total** | **82,703** | **62,554** | **20,149** | **316** | **3,594** | **3,725** |

The local MySQL report hashes were:

- CSV: `7caf4fe4a850cc23e94320cd57a3fefa087e68caf67c1ec42dacc1e391697a0c`
- Markdown: `837bc06b24d09a93cf9f0a3c06a4b72330570ae25e6dd5241f11278a5e43ecc1`

These restored-database reports remain ignored because the canonical,
source-verified reports are the tracked artifacts.

## Stage 0

| Artifact | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `tickets.parquet` | 82,703 | 21,214,427 | `b161005eb7d0b46c4df451b19dfebb129d63bbdd71c3cdc5b41a2847f583138b` |
| `people.parquet` | 316 | 10,092 | `1f4e6e9d1072d23e4d0ca05289a8f452094a73952bd384435fc8eedfc5102880` |
| `projects.parquet` | 5 | 3,316 | `3a3a7c7a15eadd92afcdac4c0c77049a6b8ba30f96d2dc4cdb3d00c847bec1e3` |

All three files were byte-identical across two same-environment exports. The
ticket file has all 24 `Ticket` fields, and `components`, `labels`, and
`project_keys` are explicitly encoded as Arrow `list<string>` even when every
observed list is empty.

Key reconciliation results:

- The project counts are DM 26,506, EVG 10,299, FAB 13,682, MESOS 10,157,
  and TIMOB 22,059.
- The 316 eligible people exactly match the threshold roster: DM 105, EVG 21,
  FAB 62, MESOS 67, and TIMOB 61. No threshold-qualified person was lost for
  lacking a retained profile bucket.
- There are 40,069 safe pre-cutoff history rows across 830 owners; 38,340 rows
  belong to the final 316-person eligible roster.
- 75,049 tickets have no temporal exclusion. The remaining 7,654 comprise
  6,079 project/key changes, 1,278 resolution-date changes, and 297 with both.
- Evidence ownership is present on 56,364 tickets and absent on 26,339. Every
  temporally excluded row has null evidence ownership.
- Stable source ID, configured project, empty-label, creation-time query source,
  project-qualified pseudonym, resolution ordering, provenance, and profile
  history checks all had zero failures.

The source audit is reproducible with:

```bash
MYSQL_URL='mysql+pymysql://root:capgraph-local@127.0.0.1:3306/tawos' \
  uv run python scripts/validate_tawos_source_audit.py
```

It passed 10/10 deterministic categories: ordinary pre- and post-cutoff rows,
creation-time summary and description edits, assignee transitions before and
after resolution, component mutation, project/key movement, resolution-date
mutation, and resolution clear followed by re-resolution. The command emits no
source issue text, issue IDs, or user IDs.

TAWOS records a resolution change for every selected-project issue with a usable
resolution boundary, so all 69,790 such rows use
`resolution_change_log_boundary`; 64,824 also have no overall temporal exclusion.
This is source behavior, not an inferred timestamp. Two DM rows contain a
resolution string without a snapshot resolution date, but both are project/key-
change exclusions with no safe boundary or owner.

## Stage 1

`data/buckets/buckets.jsonl` contained 2,668 buckets, 316 people, and 37,475
emitted tickets. It was 42,488,719 bytes with SHA-256
`9374f9e10e698a323b364bed85b1fc3d672a1005fc258b27e82e0775e40577f0`
and was byte-identical across two builds.

Bucket counts were DM 998, EVG 125, FAB 403, MESOS 461, and TIMOB 681. Bucket
IDs were unique, every size was within 3–30, the bucket-person set exactly
matched `people.parquet`, and ticket conservation, duplication, and evidence
redaction checks all had zero failures.

Before Stage 1 acceptance, a five-bucket qualitative review found one email in a
historical description. Reusable profile evidence now applies the same shared,
fixed-point privacy sanitizer as benchmark queries. Across all emitted evidence,
52 email and 691 mention placeholders were inserted; a post-build scan against
the complete Stage 0 identifier set found zero residual email, modern mention,
Jira-wiki mention, or project-qualified identifier patterns. Rechecking one
deterministic bucket per project passed privacy, temporal structure, field
redaction, and qualitative coherence 5/5.

## Benchmark manifest

| Artifact | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `benchmark_manifest.v1.jsonl` | 24,522 | 39,876,605 | `8f7cf86dd8031b7cfbfe0273f2f4bcdc70226a70bcb1285a720609459490b524` |
| `briefs.jsonl` | 150 | 242,379 | `2568ff3630b10303c121c501c6cb24aa06379e9e6736d413f7a9884086b5dbf4` |

Both files were byte-identical across two builds with manifest version
`tawos-v1.1-benchmark-v1`, seed `20260713`, and cutoff `2019-01-01`.
There were 3,320 eligible cases before deterministic sampling. The selected 150
contain 30 validation and 120 test cases, evenly split per project at 6/24.

| Project | Validation | Test | Excluded |
|---|---:|---:|---:|
| MESOS | 6 | 24 | 945 |
| FAB | 6 | 24 | 5,743 |
| TIMOB | 6 | 24 | 1,596 |
| DM | 6 | 24 | 11,559 |
| EVG | 6 | 24 | 4,529 |

The 24,372 exclusions reconcile exactly:

| Reason | Count |
|---|---:|
| `brief_too_short` | 5,342 |
| `missing_truth_assignee` | 1,365 |
| `query_not_post_cutoff` | 4,015 |
| `sampled_out` | 3,170 |
| `stage0_temporal:project_or_key_changed` | 1,456 |
| `stage0_temporal:resolution_date_changed` | 6 |
| `truth_not_eligible` | 4,992 |
| `unresolved_at_manifest_build` | 4,026 |

Candidate-set, split, exclusion, roster sorting, truth-in-roster, profile support,
brief projection, temporal order, and identifier/email/mention leakage checks all
had zero failures. Expected-zero reasons—missing source/project IDs, unsupported
query time, unsafe query text, and leakage guard failure—were all absent.

The benchmark build also exposed one sanitizer edge case before acceptance:
replacing a later mention could close an earlier unterminated Jira wiki marker
and make it newly detectable. Sanitization now normalizes whitespace first and
applies all leakage replacements to a fixed point. Regression tests cover both
multiline and newly exposed wiki mentions. The rebuilt manifest has zero
leakage-guard failures; selected brief bytes remained stable.

## Verification and deferred work

- `uv sync --all-extras --locked`: 187 packages resolved, 162 checked.
- `uv run python -m pytest -q`: 57 passed. The 14 warnings are
  Python 3.14 SQLite fixture datetime-adapter deprecations.
- `uv run ruff check .`: clean.
- No graph, retrieval, Neo4j, stage 2–5, Anthropic, or other LLM/API operation
  was run.

The benchmark/data foundation is accepted. The next implementation phase is a
separately authorized, small Stage 2 extraction pilot; it should start with
roughly 30 buckets and stop for qualitative review before any full extraction.
