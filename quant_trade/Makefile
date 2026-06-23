.PHONY: lint test format

lint:
	ruff check src tests

format:
	ruff format src tests
	ruff check --fix src tests

authors:
	@echo "Team"

test:
	pytest -q
