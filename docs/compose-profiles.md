# Compose Profiles

This project uses two compose layers for local operation:

1. `docker-compose.base.yml`: common services and defaults.
2. `docker-compose-lite.yml`: local development overrides (Postgres, Redis, lite resources).

## Recommended Lite Dev/Test Start

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml ps
```

## E2E Against Existing Lite Environment

Run Playwright against the already running lite stack:

```bash
cd frontend-angular
npm run test:e2e:lite
```

This mode enables:
- compact reporter output
- JSON + JUnit artifacts
- reuse of existing frontend backend services

## Distributed Variant

Fuer mehr Worker-Nodes:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build
```

Details: `docs/distributed-deployment.md`

## Redis Host Tuning (Windows/WSL)

If Redis warns about overcommit:

```powershell
.\docker\setup-wsl-overcommit.ps1
.\docker\setup-wsl-overcommit.ps1 -Persist
```
