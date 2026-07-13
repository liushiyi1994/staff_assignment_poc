# Implementation Plan

Ordered tasks with acceptance criteria. Work top-down; each task is a good Claude Code session. Already implemented and reusable as-is: models, settings, llm gateway, embeddings, stage1 (bucketing), stage3 (normalization), stage4 (projections), scoring + rerank, eval metrics, all prompts. Tasks below fill in the dataset- and Neo4j-dependent parts.

## Task 1 — stage0: TAWOS → parquet

1. `make db-up && make restore-tawos`, then `stage0 --introspect`; fix the SQL in `export()` and `report()` against the real schema (join issues → assignee user → project; aggregate components/labels).
2. Implement `report()`; use it to pick 4–6 domain-distinct, high-assignee-coverage projects; update `config/settings.yaml` `dataset.projects` with real keys and add a short `domain` label per project.
3. Implement `export()` including markup stripping, bot filtering, min-tickets filter.

Accept: `tickets.parquet` rows validate against `models.Ticket`; ≥150 people survive the filter; spot-check 10 rows against the MySQL source; `report()` output saved to `data/parquet/slice_report.md`.

## Task 2 — run stage1, sanity-check buckets

Accept: bucket count within 1–3× people count × avg active quarters; no bucket > `max_tickets_per_bucket`; eyeball 5 buckets for coherence (`data/buckets/buckets.jsonl`).

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

## Task 7 — eval harness

Implement `holdout.build_briefs()` (with the name-stripping leakage guard), `baselines.py` (pre-cutoff data only), and `run_eval.main()` (results table → `data/eval/results.md`, plus a bar chart PNG for the notebook).

Accept: 150 briefs built, zero briefs containing roster names (regex-verify); all 4 systems produce results; capgraph beats bm25 and most_active on Recall@5 (if it doesn't, debug retrieval before touching weights — most likely candidate-generation recall is the problem, check whether truth people appear in the candidate pool at all).

## Task 8 — demo notebook + delta batch

1. Flesh out `notebooks/demo.py` (skeleton provided): the 5-section flow in `docs/tech-design.md` §9, pyvis subgraph viz.
2. Delta-batch demo: re-run stages 1–5 with cutoff moved one quarter later, using stage5 idempotent upserts; show one person's profile before/after.

Accept: `make demo` runs top-to-bottom clean on a fresh kernel; total runtime < 10 min excluding pipeline.

## Task 9 — polish

Ruff clean, `make test` green, README setup verified on a fresh clone, record 3 strongest demo queries + eval numbers into `docs/demo-script.md`.
