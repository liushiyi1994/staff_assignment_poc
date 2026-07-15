.PHONY: setup db-up db-down restore-tawos stage0 stage1 stage2 stage3 stage4 stage5 pipeline eval test demo graph-reset

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
