# Implementation Plan

Ordered tasks with acceptance criteria. The current benchmark-foundation phase ends
after provenance, Stage 0, manifest construction, and focused fixture tests. Do
not run stages 2–5 or call an LLM during this phase. Later tasks remain here as a
roadmap and require separate authorization.

## Task 0 — pin the source artifact

Download official TAWOS v1.1 `TAWOS.sql.zip` into `data/raw/` only when absent,
verify the published MD5, compute SHA-256, and record the source record/DOI,
content version, download date, byte size, checksums, Apache-2.0 license, ethics
and research-only usage terms, and identity limitations in
`docs/data-provenance.md`. Keep the archive out of version control and do not
make another copy. This repository remains research/evaluation work and is not
production employment decision support.

Accept: the local archive is exactly 637,550,449 bytes; MD5 is
`e9c5ecc7649d55f0cf2fb4efb5664494`; provenance contains the verified SHA-256;
the ignore rules cover `data/raw/TAWOS.sql.zip`.

## Task 1 — stage0: TAWOS → parquet

1. Use the official v1.1 schema. If a compatible MySQL service is available,
   restore the dump and spot-check via `stage0 --introspect`; Docker Compose is a
   convenience, not a prerequisite for fixture-based implementation. Docker is
   unavailable in the current environment, so do not install Docker Desktop or a
   system service silently.
2. Implement `report()` across all projects. Report total/resolved tickets,
   assignee coverage, non-empty summary/description coverage, distinct assignees,
   date range, pre/post-cutoff counts, people with ≥15 pre-cutoff resolved tickets,
   and plausible post-cutoff held-out briefs. Save the deterministic report under
   `data/parquet/`.
3. Use that report to recommend 4–6 domain-distinct projects. The measured
   recommendation is MESOS, FAB, TIMOB, DM, and EVG: 82,703 source issues,
   62,554 created before cutoff, 316 people meeting the pre-cutoff ticket
   threshold, and 3,594 upper-bound plausible held-out briefs before
   retained-profile and creation-text exclusions. USERGRID/MULE yield no usable
   briefs, provisional TISTUD has only 17 after temporal exclusions, and CXX's
   15-person/23-brief pool is too small for useful Hit@10 analysis. Store an
   explicit domain for every configured project and populate
   `Bucket.project_domain`.
4. Implement `export()` against the actual Project, Issue, project-local User,
   Component, and Comment structures. Use `<project_key>:<user_id>` person IDs and
   `Person <project_key>-<user_id>` pseudonyms. Emit `labels: []` because v1.1 has
   no labels table; do not invent names or cross-project identity.
5. Apply roster and minimum-ticket filters using pre-cutoff resolved history only.
   Emit a person only when that history also produces at least one retained
   person×project×quarter Stage 1 bucket under the configured size bounds.
   V1.1's opaque user IDs do not permit name-based bot detection; do not filter a
   person using invented signals or the generated pseudonym. Clean Jira/HTML
   markup and aggregate components deterministically for Stage 0 audit. Reconstruct
   creation text and resolution owner from `Change_Log`; exclude project/key moves,
   explicit resolution-date edits, undated resolution changes, and latest
   transitions that clear resolution from temporal evidence. Preserve a stable
   numeric source issue ID. Comments are deliberately
   excluded rather than used as a fallback. Unversioned component names and final
   assignment/status fields must be redacted from Stage 1 evidence.

Accept: normalized rows validate against `models.Ticket`; join, pseudonym, markup,
component audit, project-domain, report-statistic, change-log exclusion, and pre-cutoff roster behavior pass
real-schema fixtures; `data/parquet/slice_report.*` is reproducible, source-verified,
and accompanied by digest/effective-parameter metadata. When MySQL is
available, spot-check 10 exported rows against the restored source.

## Task 2 — run stage1, sanity-check buckets

Accept later: bucket count within 1–3× people count × avg active quarters; every
qualifying ticket appears exactly once after deterministic rebalancing; every
Stage 0 person has a retained bucket; no bucket violates configured size bounds
when a valid partition exists; evidence tickets contain no final outcome or
unversioned component-name fields; eyeball 5 buckets for coherence
(`data/buckets/buckets.jsonl`).

## Task 3 — stage2 extraction + quality pass

1. Run on ~30 buckets first; read every output; iterate on `prompts/extraction.md` until summaries are concrete and skills are evidence-grounded (expect 2–3 prompt iterations).
2. Full run. Then sample 5% of contributions and grade them with the strong model (build a tiny `scripts/grade_sample.py`): grounded? specific? skills supported?

Accept: ≥90% of graded sample passes; total stage cost logged < $15; skip-rate < 25%.

## Task 4 — run stage3 + manual term review, stage4

Review `terms.jsonl`, populate `term_overrides.yaml` (budget 1 hour), re-run stage3, run stage4.

Accept: no embarrassing merges in top-100 skills by frequency; canonical skills roughly 300–600; `capabilities.jsonl` validates.

## Task 5 — stage5: Neo4j load

Implement `load()`: batched UNWIND MERGE upserts for all nodes/edges per `docs/tech-design.md` §4; embed contribution summaries (`embeddings.embed`) into `Contribution.embedding`; derive `COLLABORATED_WITH` from bucket co-occurrence (same project+period); idempotent re-runs.

Accept: node/edge counts printed and plausible; `CALL db.index.vector.queryNodes('contribution_embedding', 5, $vec)` returns sensible neighbors for a hand-written probe text; re-running stage5 does not duplicate anything.

## Task 6 — query engine: retrieve.py

Implement `generate_candidates` (vector arm + structured arm, union, parameterized Cypher) and `expand` (fill capabilities + relevant contributions). Then run 5 hand-written briefs end-to-end (`python -m capgraph.query.engine "..."`) and eyeball shortlists.

Accept: end-to-end query < 15s; every ranked person's reason cites evidence that exists in their profile; at least one brief shows a person found by vector arm only (proves union matters).

## Task 7 — deterministic temporal benchmark foundation

1. Define query time from issue creation or a defensible recorded assignment event,
   never eventual resolution. Expose no description/comment/evidence created after
   query time. Freeze candidate eligibility and activity features from pre-query
   history, and calculate recency at the cutoff/query time rather than today.
2. Build a deterministic, versioned manifest containing stable TAWOS issue ID,
   final Jira key for audit, query text, as-of time, project, same-project eligible
   roster, truth IDs, split, and exclusion reason.
   Use a fixed seed and deterministic project stratification. Strip explicit
   project-qualified IDs/pseudonyms, mentions, and emails from query text.
3. Keep every baseline on the identical historical information budget. The
   assignee reconstructed at the safe resolution boundary is ground truth for
   assignee prediction, not evidence of optimal fit; final assignment is audit-only.
4. Rename the existing binary Recall@K behavior to Hit@K (or separately implement
   true set Recall@K). Plan overall and per-project Hit@1/5/10, MRR, candidate
   recall, latency, and cost.

Accept for the foundation phase: manifest bytes are stable across repeated builds
with the same inputs/version/seed; every included truth ID is in its recorded
eligible roster; every selected truth and roster ID has a retained Stage 1 profile
bucket; leakage guard tests reject future evidence and identifiers; split and
exclusion counts reconcile with source candidates. Running retrieval systems and
LLM evaluation is deferred.

## Task 8 — demo notebook + delta batch

1. Flesh out `notebooks/demo.py` (skeleton provided): the 5-section flow in `docs/tech-design.md` §9, pyvis subgraph viz.
2. Delta-batch demo: re-run stages 1–5 with cutoff moved one quarter later, using stage5 idempotent upserts; show one person's profile before/after.

Accept later: `make demo` runs top-to-bottom clean on a fresh kernel; total runtime < 10 min excluding pipeline. Synthetic profiles, if ever used in a qualitative demo, remain physically separate from and are never scored in the quantitative benchmark.

## Task 9 — polish

Ruff clean, `make test` green, README setup verified on a fresh clone, provenance
and generated-artifact instructions current, and (in the later evaluation phase)
record 3 strongest demo queries + eval numbers into `docs/demo-script.md`.
