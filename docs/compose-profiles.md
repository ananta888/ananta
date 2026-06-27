# Compose Profiles

Diese Seite ordnet die Compose-Varianten zuerst nach Nutzerziel. Die Dateinamen sind wichtig, aber die Auswahl sollte ueber den Zweck erfolgen.

## Auswahl nach Ziel

| Ziel | Variante | Wann nutzen |
| --- | --- | --- |
| Demo oder erster lokaler Lauf | `compose.stack.quickstart.yml` | SQLite, zwei Worker, wenig Infrastrukturwissen. |
| Alltagliche lokale Entwicklung mit LM Studio | `compose.dev.lmstudio.yml` | Bind-Mounts und automatische Reloads. |
| Alltagliche lokale Entwicklung mit Ollama | `compose.dev.ollama.yml` | Bind-Mounts plus lokaler Ollama-Service. |
| Persistenter Fullstack | `compose.stack.full.yml` | PostgreSQL, Redis, Hub und zwei Worker. |
| Mehrere Worker oder verteiltes Setup | `compose.stack.distributed.yml` | Vier Worker und optionale Ollama-Runtime. |
| Isolierte E2E/CI-Laeufe | `docker/old_way/` | Bestehende Spezial-Overlays bis zu ihrer separaten Migration. |

## Basis-Schichten

Neue Starts verwenden genau eine Datei aus `docker/compose-next/`. Gemeinsame
Service-Defaults liegen intern in `compose.base.yml` und werden über
`extends` eingebunden. Die Datei wird nicht separat gestartet.

Environment templates:

- `.env.example`: ready-to-copy defaults for the common lite stack.
- `.env.template`: compact template when secrets, ports and provider URLs should be filled explicitly.
- Für `compose.stack.distributed.yml` können die Worker-IDs über
  `ANANTA_WORKER_GAMMA_ID` und `ANANTA_WORKER_DELTA_ID` überschrieben werden.
- Legacy-Testvariablen bleiben in `docker/old_way/README.md` und
  `docs/testing.md` dokumentiert.

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

## Empfohlener lokaler Start

Nutze diesen Pfad fuer Demo, Standard lokal und die meisten schnellen Tests:

```bash
docker compose --env-file .env -f docker/compose-next/compose.stack.quickstart.yml up -d --build
docker compose --env-file .env -f docker/compose-next/compose.stack.quickstart.yml ps
```

Entwicklung mit Ollama:

```bash
POSTGRES_PASSWORD=... \
docker compose --env-file .env -f docker/compose-next/compose.dev.ollama.yml up -d --build
```

Cleanup:
```bash
docker compose --env-file .env -f docker/compose-next/compose.stack.quickstart.yml down -v --remove-orphans
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
POSTGRES_PASSWORD=... \
docker compose --env-file .env -f docker/compose-next/compose.stack.distributed.yml up -d --build
```

Ollama kann bei dieser Variante über `--profile ollama` aktiviert werden.

Details: `docs/distributed-deployment.md`

## Redis Host Tuning (Windows/WSL)

If Redis warns about overcommit:

```powershell
.\docker\setup-wsl-overcommit.ps1
.\docker\setup-wsl-overcommit.ps1 -Persist
```
