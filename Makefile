.PHONY: help check check-fast check-deep format release-gate worker-standalone-build

help:
	@echo "Ananta Development Makefile"
	@echo "Usage:"
	@echo "  make check        - Run standard check pipeline (format, lint, types, arch, fast tests)"
	@echo "  make check-fast   - Run fast check pipeline (format, lint only)"
	@echo "  make check-deep   - Run deep check pipeline (all checks + deep tests)"
	@echo "  make release-gate - Check if repo is ready for release"
	@echo "  make format       - Format code with ruff"
	@echo "  make worker-standalone-build - Build standalone worker image"

check:
	python scripts/check_pipeline.py --mode standard

check-fast:
	python scripts/check_pipeline.py --mode fast

check-deep:
	python scripts/check_pipeline.py --mode deep

release-gate:
	python scripts/release_gate.py

format:
	ruff format .
	ruff check --fix .

worker-standalone-build:
	docker build -f Dockerfile.worker-standalone -t ananta-worker-standalone:local .
