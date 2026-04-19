# Compose Profiles

Diese Seite ordnet die Compose-Varianten zuerst nach Nutzerziel. Die Dateinamen sind wichtig, aber die Auswahl sollte ueber den Zweck erfolgen.

## Auswahl nach Ziel

| Ziel | Variante | Wann nutzen |
| --- | --- | --- |
| Demo oder erster lokaler Lauf | Lite-Stack | Schnellster Einstieg, wenig Infrastrukturwissen noetig. |
| Alltagliche lokale Entwicklung | Lite-Stack plus `setup.ps1` | Standard fuer lokale Arbeit mit reproduzierbarer `.env`. |
| Live-Code und Browser-Test | Live-Code-Stack | Wenn Python/Angular-Aenderungen sofort im Container sichtbar sein sollen. |
| Lokale LLM-Runtime unter WSL2/Vulkan | Lite + Ollama-WSL Overlay | Wenn Ollama im Compose-Stack GPU-nah laufen soll. |
| Mehrere Worker oder verteiltes Setup | Distributed Stack | Wenn Routing und Worker-Verteilung getestet werden sollen. |
| Isolierte E2E/CI-Laeufe | Test-Stack | Wenn Playwright oder CI ohne Host-Port-Konflikte laufen soll. |

## Basis-Schichten

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

Nutze diesen Pfad fuer Demo, Standard lokal und die meisten schnellen Tests:

```bash
scripts/compose-test-stack.sh down
scripts/compose-test-stack.sh up
scripts/compose-test-stack.sh ps
```

WSL2 mit Vulkan fuer den Compose-Ollama-Service:

```bash
scripts/compose-test-stack.sh down
scripts/compose-test-stack.sh up
scripts/compose-test-stack.sh ps
```

Sicheres Deep-Cleanup (Volumes ausser `ollama_data`):
```bash
scripts/compose-test-stack.sh clean
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

Nutze diese Variante erst, wenn du mehrere Worker-Nodes oder Routing-Verhalten pruefen willst.

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
