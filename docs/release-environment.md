# Release Environment

This document defines the supported runtime and build environment for the v1.0.0 release track.

## Language Runtimes

- Backend runtime: Python `3.11`, containerized with `python:3.11.15-slim-bookworm`.
- Backend dependency source: `requirements.lock` for runtime, `requirements-dev.lock` for CI-only test and lint tooling.
- Frontend runtime and CI: Node `20.19.5`.
- Frontend dependency source: `frontend-angular/package-lock.json` through `npm ci`.

## Container Images

Release-relevant Ananta containers use explicit tags plus registry digests:

- Backend: `python:3.11.15-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47`
- Frontend: `node:20.19.5-slim@sha256:9e70124bd00f47dd023e349cd587132ae61892acc0e47ed641416c3e18f401c3`
- Evolver bridge: `node:20.19.5-bookworm-slim@sha256:9e70124bd00f47dd023e349cd587132ae61892acc0e47ed641416c3e18f401c3`
- Ollama: `ollama/ollama:0.20.7@sha256:487324a9312240e3e122446f351b1f1e3f68d884ef854c246db2e08792440d94`
- Alpine helper: `alpine:3.23.4@sha256:5b10f432ef3da1b8d4c7eb6c487f2f5a8f096bc91145e68878dd4a5019afde11`
- PostgreSQL: `postgres:16.13@sha256:5a65324fe84dc41709ff914e90b07f3e2f577073ed27bf917d4873aca0c9ec51`
- Redis: `redis:7.4.8-alpine@sha256:7aec734b2bb298a1d769fd8729f13b8514a41bf90fcdd1f38ec52267fbaa8ee6`
- Nginx: `nginx:1.29.4-alpine3.23@sha256:4870c12cd2ca986de501a804b4f506ad3875a0b1874940ba0a2c7f763f1855b2`
- Loki: `grafana/loki:3.6.10@sha256:f59e6aedfee6af2388d69962b178a35a23152573b4bf0937106746deb7146ec7`
- Promtail: `grafana/promtail:3.6.10@sha256:2a0f5e3e160ee5d549c585f6cc4f4e1c566ff783324a424bd75bc16503fc660e`
- Grafana: `grafana/grafana:12.3.0@sha256:70d9599b186ce287be0d2c5ba9a78acb2e86c1a68c9c41449454d0fc3eeb84e8`
- Prometheus: `prom/prometheus:v3.11.2@sha256:5550dc63da361dc30f6fe02ac0e4dfc736ededfef3c8d12a634db04a67824d78`
- Certbot: `certbot/certbot:v5.5.0@sha256:78a7eaa40af657301e25731297b6e73646c695370d17e9bb03189fa82966d65c`

The local WSL/Vulkan Ollama image is tagged as `ollama-wsl-amd:0.20.7-vulkan` and is built from `Dockerfile.ollama-wsl-amd`.

The Taiga example stack under `docs/taiga/` is not part of the Ananta v1.0.0 release runtime. It is pinned separately with image digests so it does not reintroduce floating `latest` pulls.

## Tooling

- Backend image global CLI: `opencode-ai@1.14.18`.
- CI architecture diagram renderer: `@mermaid-js/mermaid-cli@11.12.0`.
- Docker Compose release validation should use the pinned compose files and not override image references with floating tags.

## Residual Drift

Docker image tags are digest-pinned. The remaining system package drift comes from `apt-get` resolving packages from the current Debian repository for the pinned base image suite.

For a stricter production release, move apt-installed runtime tools into a maintained internal base image or Debian snapshot-backed build.
