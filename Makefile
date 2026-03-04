.PHONY: lint test build

lint:
	ruff check . && ruff format --check .

test:
	pytest

build:
	@echo "build not yet implemented"
