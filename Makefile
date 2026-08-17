.PHONY: setup db-up db-down restore-tawos stage0 stage1 stage2 stage2-pilot-manifest stage2-pilot-dry-run stage3 stage4 stage5 pipeline eval eval-baselines eval-validation eval-test eval-v2-levers eval-v2-sweep eval-v2-scores eval-v2-validation eval-v2-test eval-v2-report eval-v3-scores eval-v3-pool-levers eval-v3-validation eval-v3-baselines eval-v3-test eval-v3-report eval-v4-manifest eval-v4-rewrite eval-v4-baselines eval-v4-validation eval-v4-test eval-v4-report wave1 wave1-parity wave1-probe wave1-probe-report rerank-pin rerank-pin-verify rerank-arm rerank-report sweeps-verify-graph sweeps-graph-up sweeps-graph-down sweeps-study-graph sweeps-noise-floor sweeps-replay sweeps-gates sweeps-arm sweeps-report nearmiss-structure nearmiss-rewrite nearmiss-run nearmiss-report test demo graph-reset

PILOT_MANIFEST := data/contributions/pilot_manifest.v1.jsonl

setup:
	uv sync --all-extras

db-up:
	docker compose up -d

db-down:
	docker compose down

# One-time. Streams the official zip directly so the 4.3 GB SQL member is not duplicated.
restore-tawos:
	@if [ -f data/raw/TAWOS.sql.zip ]; then \
		echo "Streaming data/raw/TAWOS.sql.zip into MySQL ..."; \
		bash -o pipefail -c 'unzip -p data/raw/TAWOS.sql.zip | docker compose exec -T mysql mysql --max_allowed_packet=1G -uroot -pcapgraph-local tawos'; \
	else \
		echo "Download the official TAWOS.sql.zip into data/raw/ first"; \
		exit 1; \
	fi

stage0:
	uv run python -m capgraph.pipeline.stage0_load
stage1:
	uv run python -m capgraph.pipeline.stage1_bucket
stage2:
	uv run python -m capgraph.pipeline.stage2_extract

# Pilot gate: both targets are offline. Running the pilot itself spends money and
# stays an explicit, separately authorized command (see README).
stage2-pilot-manifest:
	uv run python -m capgraph.pipeline.stage2_pilot
stage2-pilot-dry-run:
	uv run python -m capgraph.pipeline.stage2_extract --pilot $(PILOT_MANIFEST) --dry-run
stage3:
	uv run python -m capgraph.pipeline.stage3_normalize
stage4:
	uv run python -m capgraph.pipeline.stage4_project
stage5:
	uv run python -m capgraph.pipeline.stage5_graph

pipeline: stage0 stage1 stage2 stage3 stage4 stage5

graph-reset:
	uv run python -m capgraph.pipeline.stage5_graph --reset

# Offline: rebuild data/eval/results.{md,json} and docs/eval-results.md from the
# checkpointed runs. Makes no model call and re-runs nothing.
eval:
	uv run python -m capgraph.eval.run_eval --report-only

# Offline: the three baselines over both splits. No model call, no Neo4j.
eval-baselines:
	uv run python -m capgraph.eval.run_eval --split all --systems bm25,vector_only,most_active

# These two SPEND: intent + re-rank per case, logged and budgeted under
# llm/eval stage_name. Run validation first, freeze the configuration, then test once.
eval-validation:
	uv run python -m capgraph.eval.run_eval --split validation
eval-test:
	uv run python -m capgraph.eval.run_eval --split test

# --- benchmark v2 (docs/work-orders/benchmark-v2.md) ---
# Offline: rank-level levers (RRF fusion, roster backstop) recombined from the frozen
# v1 checkpoints, and the weight sweep over the score-component checkpoint.
eval-v2-levers:
	uv run python -m capgraph.eval.run_v2 --levers
eval-v2-sweep:
	uv run python -m capgraph.eval.scores --sweep --split validation

# SPENDS (intent parse only): checkpoints the score components the sweep reads.
eval-v2-scores:
	uv run python -m capgraph.eval.scores --split validation --stage stage7b_val

# These two SPEND under stage7b_val / stage7b_test, into data/eval/v2/runs/. Run the
# validation arms, freeze docs/benchmark-v2-config.md, then run test exactly once.
eval-v2-validation:
	uv run python -m capgraph.eval.run_v2 --split validation
eval-v2-test:
	uv run python -m capgraph.eval.run_v2 --split test

# Offline: rebuild the v2 section of docs/eval-results.md (leaves the v1 half alone).
eval-v2-report:
	uv run python -m capgraph.eval.run_v2 --report

# --- benchmark v3 (docs/work-orders/benchmark-v3.md) ---
# SPENDS (intent parse only): checkpoints the score components, with the lexical arm
# on, that the offline pool/window analysis below reads.
eval-v3-scores:
	uv run python -m capgraph.eval.run_v3 --scores --split validation

# Offline: candidate recall and window recall with and without the lexical arm, at
# each candidate window width. This is where levers 1 and 3 are decided.
eval-v3-pool-levers:
	uv run python -m capgraph.eval.run_v3 --pool-levers --split validation

# SPENDS under stage7c_val, into data/eval/v3/runs/<arm>/. One arm per lever; pass the
# arm name, e.g. `make eval-v3-validation ARM=ab_cards`.
eval-v3-validation:
	uv run python -m capgraph.eval.run_v3 --split validation --variant $(ARM) \
		--systems capgraph_full,capgraph_score

# Offline: the three baselines into the frozen v3 namespaces. Run after freezing.
eval-v3-baselines:
	uv run python -m capgraph.eval.run_v3 --split validation \
		--variant $(shell uv run python -c "from capgraph.settings import settings; print(settings['eval.v3.frozen_validation_variant'])") \
		--systems bm25,vector_only,most_active
	uv run python -m capgraph.eval.run_v3 --split test --systems bm25,vector_only,most_active

# SPENDS under stage7c_test. Run exactly once, after docs/benchmark-v3-config.md is
# frozen. This is the third and last exposure of this manifest's test split.
eval-v3-test:
	uv run python -m capgraph.eval.run_v3 --split test

# Offline: rebuild the v3 section of docs/eval-results.md (leaves v1 and v2 alone).
eval-v3-report:
	uv run python -m capgraph.eval.run_v3 --report

# --- benchmark v4 (docs/work-orders/benchmark-v4.md) ---
# Offline: build the work-package manifest from the Stage 0 exports. Deterministic and
# free; it freezes whatever rewrites are already checkpointed and excludes the rest.
eval-v4-manifest:
	uv run python -m capgraph.eval.packages

# SPENDS under bench4_rewrite: one cheap-model rewrite per package, checkpointed and
# then frozen into the manifest. Re-running it after every package has a rewrite is a
# no-op, which is what makes the manifest reproducible without re-paying.
eval-v4-rewrite:
	uv run python -m capgraph.eval.rewrite

# Offline: the three baselines in every namespace the report reads (both engine
# configurations, and the raw-brief variant on validation). No model call.
eval-v4-baselines:
	uv run python -m capgraph.eval.run_v4 --split all --baselines --engine v3frozen
	uv run python -m capgraph.eval.run_v4 --split all --baselines --engine v2frozen
	uv run python -m capgraph.eval.run_v4 --split validation --baselines \
		--engine v3frozen --briefs raw

# These SPEND under bench4_val / bench4_test. Pass ENGINE=v2frozen or v3frozen, and
# BRIEFS=raw for the rewrite-effect arm. The test split is run once per engine.
eval-v4-validation:
	uv run python -m capgraph.eval.run_v4 --split validation --engine $(ENGINE) \
		--briefs $(or $(BRIEFS),rewritten) --systems capgraph_full,capgraph_score
eval-v4-test:
	uv run python -m capgraph.eval.run_v4 --split test --engine $(ENGINE) \
		--systems capgraph_full,capgraph_score

# Offline: rebuild the v4 section of docs/eval-results.md (leaves v1-v3 alone).
eval-v4-report:
	uv run python -m capgraph.eval.run_v4 --report

# --- improvement wave 1 (docs/work-orders/improvement-wave1.md) ---
# Offline: every measurement in docs/improvement-wave1-report.md. Makes no model call.
# The G1 truncation measurement reads the untruncated source text, so it needs MYSQL_URL;
# the other four read data/ only.
wave1:
	uv run python -m capgraph.eval.wave1 --all

# Offline: the acceptance check on its own — the three deterministic baselines re-run and
# diffed against the frozen v3 validation namespace, plus the configuration digest.
wave1-parity:
	uv run python -m capgraph.eval.wave1 --parity

# SPENDS under stage `probe_order`, against its own $2 ceiling (backlog G7). One arm: the
# frozen v3 configuration with the re-rank window presented worst-first.
wave1-probe:
	uv run python -m capgraph.eval.probe_order --run

# Offline: re-read the probe's checkpoint and rebuild its tables and verdict.
wave1-probe-report:
	uv run python -m capgraph.eval.probe_order --report

# --- re-rank prompt redesign (docs/work-orders/rerank-redesign.md) ---
# SPENDS under stage `rerank_redesign`, against its own $6 ceiling. Needs Neo4j: one
# intent parse and one retrieval pass per v4 validation case, captured so every arm can
# replay the identical pools. Resumable; nothing already captured is paid for twice.
rerank-pin:
	uv run python -m capgraph.eval.rerank_redesign --capture-pin

# Offline: does the captured retrieval reproduce the frozen v4 validation run?
rerank-pin-verify:
	uv run python -m capgraph.eval.rerank_redesign --verify-pin

# SPENDS under the same stage and ceiling: one arm, re-rank calls only, no Neo4j.
# ARM=A current prompt reversed, B redesigned ordered, C redesigned reversed.
rerank-arm:
	uv run python -m capgraph.eval.rerank_redesign --arm $(ARM)

# Offline: rebuild docs/rerank-redesign-report.md from the arms that have run.
rerank-report:
	uv run python -m capgraph.eval.rerank_redesign --report

# --- deterministic-side sweeps (docs/work-orders/deterministic-sweeps.md) ---
# Offline: read the production graph's counts and record them against the work order's.
# Run at both ends of the study. WHEN=before|after labels the observation.
sweeps-verify-graph:
	uv run python -m capgraph.eval.sweeps --verify-graph $(or $(WHEN),ad-hoc)

# The isolated study graph for G3a: a throwaway second Neo4j on bolt 7688, so the
# production graph is never put into a study state. Start it, build the gated
# vocabulary into it, and remove it (container AND volume) when the study is done.
sweeps-graph-up:
	docker run -d --name capgraph-sweeps-neo4j -p 7688:7687 -p 7475:7474 \
		-e NEO4J_AUTH=neo4j/capgraph-local -e NEO4J_dbms_memory_heap_max__size=2G \
		-v capgraph_sweeps_neo4j_data:/data neo4j:5-community
sweeps-graph-down:
	docker rm -f capgraph-sweeps-neo4j && docker volume rm capgraph_sweeps_neo4j_data

# Offline: Stage 3 with the G3a floor on, Stage 4, and Stage 5 into the study graph.
# Writes only under data/eval/sweeps/; reads the production raw extraction and the
# contribution embedding cache.
sweeps-study-graph:
	uv run python -m capgraph.eval.sweeps --build-study-graph

# SPENDS under stage `noise_floor`, against the study's $8 ceiling: one repeat of the
# rerank-redesign baseline arm on its own pin. This is the gauge every other comparison
# in the study is read against.
sweeps-noise-floor:
	uv run python -m capgraph.eval.sweeps --noise-floor

# Offline, $0: replay the pinned parses under one condition. CONDITION=base, g3a_df3
# or g6_strength. Needs Neo4j (the production graph, or the study graph for g3a_df3).
sweeps-replay:
	uv run python -m capgraph.eval.sweeps --replay $(CONDITION)

# Offline: both tier-2 gates, evaluated from the checkpoints.
sweeps-gates:
	uv run python -m capgraph.eval.sweeps --gates

# SPENDS under stage `sweep_val`: one full-system arm on a gated condition's own pin.
# Only run this when `sweeps-gates` says the lever's gate passed.
sweeps-arm:
	uv run python -m capgraph.eval.sweeps --arm $(CONDITION)

# Offline: rebuild docs/deterministic-sweeps-report.md from whatever has run.
sweeps-report:
	uv run python -m capgraph.eval.sweeps --report

# --- near-miss quality study (docs/work-orders/nearmiss-study.md) ---
# Offline and free: rebuild the sibling v4 manifest from the Stage 0 exports and verify
# it against the published record. Refuses to write anything on drift. Every package's
# structure is built; only the validation split ever gets a rewrite.
nearmiss-structure:
	uv run python -m capgraph.eval.nearmiss --structure

# SPENDS under nearmiss_rewrite: one cheap-model rewrite per *validation* package. A
# no-op once each has one. No test brief is ever sent to the rewriter.
nearmiss-rewrite:
	uv run python -m capgraph.eval.nearmiss --rewrite

# SPENDS under nearmiss_val: one run of the full graph system over the 28 validation
# cases, into data/eval/nearmiss/runs/. Resumes from its checkpoint if interrupted.
nearmiss-run:
	uv run python -m capgraph.eval.nearmiss --run

# Offline: recompute the pre-specified metrics and rewrite docs/nearmiss-study.md.
nearmiss-report:
	uv run python -m capgraph.eval.nearmiss --report

test:
	uv run python -m pytest -q

demo:
	uv run jupytext --to notebook notebooks/demo.py && uv run jupyter lab notebooks/demo.ipynb
