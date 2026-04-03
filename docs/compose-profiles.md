# Compose Profiles

This project uses two compose layers for local operation:

1. `docker-compose.base.yml`: common services and defaults.
2. `docker-compose-lite.yml`: local development overrides (Postgres, Redis, lite resources).

Environment templates:

- `.env.example`: ready-to-copy defaults for the common lite stack.
- `.env.template`: compact template when secrets, ports and provider URLs should be filled explicitly.
- For `docker-compose.distributed.yml`, additionally set `AGENT_TOKEN_GAMMA`, `AGENT_TOKEN_DELTA`, `GAMMA_PORT` and `DELTA_PORT`.
- For `docker-compose.test.yml`, the optional live-test knobs (`RUN_LIVE_LLM_TESTS`, `LIVE_LLM_MODEL`, `LIVE_LLM_TIMEOUT_SEC`, `LIVE_LLM_RETRY_ATTEMPTS`, `LIVE_LLM_RETRY_BACKOFF_SEC`, `E2E_OLLAMA_URL`, `E2E_LMSTUDIO_URL`, `E2E_ADMIN_PASSWORD`) are now listed in the env templates as well.

## Runtime Profiles

Runtime profile selection is explicit via `ANANTA_RUNTIME_PROFILE` (default in compose: `compose-safe`).

Supported profiles:

- `local-dev`
- `trusted-lab`
- `compose-safe`
- `distributed-strict`

Visibility:

- `GET /config` exposes `runtime_profile_effective`.
- `GET /dashboard/read-model` exposes `llm_configuration.runtime_profile`.
- `GET /health` exposes `checks.runtime_profile` with validation status.

Validation:

- `POST /config` rejects unknown `runtime_profile` values with `invalid_runtime_profile`.

## Recommended Lite Dev/Test Start

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml ps
```

WSL2 mit Vulkan fuer den Compose-Ollama-Service:

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml up -d --build
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml ps
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
- watchdog timeout (`E2E_LITE_TIMEOUT_MINUTES`, default `25`)
- concise failure summary from `test-results/results.json`

## Distributed Variant

Fuer mehr Worker-Nodes:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build
```

Mit WSL2/Vulkan fuer den Compose-Ollama-Service:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.ollama-wsl.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build
```

Details: `docs/distributed-deployment.md`

## Redis Host Tuning (Windows/WSL)

If Redis warns about overcommit:

```powershell
.\docker\setup-wsl-overcommit.ps1
.\docker\setup-wsl-overcommit.ps1 -Persist
```
