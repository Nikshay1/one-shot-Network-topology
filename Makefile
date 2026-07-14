# VERDICT — make targets. uv-compatible.
.DEFAULT_GOAL := golden
.PHONY: setup golden test lint clean

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

lint:
	uv run ruff check .

clean:
	rm -rf .pytest_cache **/__pycache__ data/*.parquet
