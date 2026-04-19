# Release Environment

This document defines the supported runtime and build environment for the v1.0.0 release track.

## Language Runtimes

- Backend runtime: Python `3.11`, containerized with `python:3.11.15-slim-bookworm`.
- Backend dependency source: `requirements.lock` for runtime, `requirements-dev.lock` for CI-only test and lint tooling.
- Frontend runtime and CI: Node `20.19.5`.
- Frontend dependency source: `frontend-angular/package-lock.json` through `npm ci`.

## Container Images

Release-relevant Ananta containers use explicit tags:

- Backend: `python:3.11.15-slim-bookworm`
- Frontend: `node:20.19.5-slim`
- Evolver bridge: `node:20.19.5-bookworm-slim`
- Ollama: `ollama/ollama:0.20.7`
- Alpine helper: `alpine:3.23.4`
- PostgreSQL: `postgres:16.13`
- Redis: `redis:7.4.8-alpine`
- Nginx: `nginx:1.29.4-alpine3.23`
- Loki: `grafana/loki:3.6.10`
- Promtail: `grafana/promtail:3.6.10`
- Grafana: `grafana/grafana:12.3.0`
- Prometheus: `prom/prometheus:v3.11.2`
- Certbot: `certbot/certbot:v5.5.0`

The local WSL/Vulkan Ollama image is tagged as `ollama-wsl-amd:0.20.7-vulkan` and is built from `Dockerfile.ollama-wsl-amd`.

The Taiga example stack under `docs/taiga/` is not part of the Ananta v1.0.0 release runtime. Its image policy must be handled separately before using that stack as a production deployment artifact.

## Tooling

- Backend image global CLI: `opencode-ai@1.14.18`.
- CI architecture diagram renderer: `@mermaid-js/mermaid-cli@11.12.0`.
- Docker Compose release validation should use the pinned compose files and not override image references with floating tags.

## Residual Drift

Docker image tags are explicit but not yet digest-pinned. The remaining system package drift comes from `apt-get` resolving packages from the current Debian repository for the pinned base image suite.

For a stricter production release, replace exact tags with digests and move apt-installed runtime tools into a maintained internal base image or Debian snapshot-backed build.
