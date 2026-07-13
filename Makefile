.PHONY: setup db-up db-down restore-tawos stage0 stage1 stage2 stage3 stage4 stage5 pipeline eval test demo graph-reset

setup:
	uv sync --all-extras

db-up:
	docker compose up -d

db-down:
	docker compose down

# One-time. Expects the TAWOS .sql dump in data/raw/. Slow (large dump).
restore-tawos:
	@DUMP=$$(ls data/raw/*.sql 2>/dev/null | head -1); \
	if [ -z "$$DUMP" ]; then echo "Put the TAWOS .sql dump in data/raw/ first"; exit 1; fi; \
	echo "Restoring $$DUMP ..."; \
	docker compose exec -T mysql sh -c 'mysql -uroot -pcapgraph-local tawos' < $$DUMP

stage0:
	uv run python -m capgraph.pipeline.stage0_load
stage1:
	uv run python -m capgraph.pipeline.stage1_bucket
stage2:
	uv run python -m capgraph.pipeline.stage2_extract
stage3:
	uv run python -m capgraph.pipeline.stage3_normalize
stage4:
	uv run python -m capgraph.pipeline.stage4_project
stage5:
	uv run python -m capgraph.pipeline.stage5_graph

pipeline: stage0 stage1 stage2 stage3 stage4 stage5

graph-reset:
	uv run python -m capgraph.pipeline.stage5_graph --reset

eval:
	uv run python -m capgraph.eval.run_eval

test:
	uv run pytest -q

demo:
	uv run jupytext --to notebook notebooks/demo.py && uv run jupyter lab notebooks/demo.ipynb
