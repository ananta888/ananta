.PHONY: help check check-fast check-deep format

help:
	@echo "Ananta Development Makefile"
	@echo "Usage:"
	@echo "  make check        - Run standard check pipeline (format, lint, types, arch, fast tests)"
	@echo "  make check-fast   - Run fast check pipeline (format, lint only)"
	@echo "  make check-deep   - Run deep check pipeline (all checks + deep tests)"
	@echo "  make format       - Format code with ruff"

check:
	python scripts/check_pipeline.py --mode standard

check-fast:
	python scripts/check_pipeline.py --mode fast

check-deep:
	python scripts/check_pipeline.py --mode deep

format:
	ruff format .
	ruff check --fix .
