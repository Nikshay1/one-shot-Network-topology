# VERDICT — make targets. uv-compatible.
.DEFAULT_GOAL := golden
.PHONY: setup golden test lint clean run harden warm-cache bench

PY   ?= py
HOST ?= 127.0.0.1
PORT ?= 8000

# Create the environment and install VERDICT + deps.
setup:
	uv venv
	uv pip install -e .

# The self-checking golden harness (contracts, normalize, + later pipeline checks).
golden:
	bash scripts/golden.sh

# Full test suite.
test:
	uv run pytest

# Serve the API (contracts/api_contract.md v1.1) on $(HOST):$(PORT).
run:
	$(PY) -m backend.main --host $(HOST) --port $(PORT)

# Fire demo scenario N end-to-end: reset run state, assert warm caches, run it.
# Uses the API if it is already up, otherwise runs the pipeline in-process.
#   make demo-1        OFFLINE=1 make demo-3        make demo-list
demo-list:
	$(PY) -m backend.narrate.cache --list

demo-%:
	bash scripts/demo.sh $*

# Pre-render every demo scenario so an OFFLINE demo makes zero API calls.
warm-cache:
	$(PY) -m backend.narrate.cache --warm-cache --all-demo-scenarios

# The numbers: split, both modes, ablations, then the report.
bench:
	$(PY) -m eval.split
	$(PY) -m eval.run_benchmark --heldout --agentic
	$(PY) -m eval.run_benchmark --heldout --fixed-pipeline --with-ablations
	$(PY) -m eval.report

# Cold-start + kill-network hardening of every demo path.
harden:
	bash scripts/harden.sh

lint:
	uv run ruff check .

clean:
	rm -rf .pytest_cache **/__pycache__ data/*.parquet
