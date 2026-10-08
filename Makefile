.PHONY: setup lint format typecheck test run-sample fixtures

setup:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy tourgen

test:
	uv run pytest

fixtures:
	uv run python scripts/make_fixtures.py

run-sample:
	uv run tourgen run tests/fixtures/photos --job-id sample --fake-generation --fake-llm --yes
