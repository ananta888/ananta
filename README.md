# Ananta

Modulares Multi-Agent-System fuer AI-gestuetzte Entwicklung mit Hub-Worker-Architektur.

## Einstiegspunkte
- Architektur und Zielbild: `architektur/README.md`, `docs/autonomous-platform-target-model.md`
- Backend API und Betrieb: `agent/README.md`, `docs/backend.md`, `api-spec.md`
- Frontend Entwicklung und E2E: `frontend-angular/README.md`
- Setup/Runtime: `docs/INSTALL_TEST_BETRIEB.md`, `docs/DOCKER_WINDOWS.md`

## Architektur
- Angular Frontend fuer Visualisierung und Steuerung
- Hub-Agent fuer Orchestrierung (Tasks, Teams, Templates)
- Worker-Agenten fuer LLM-gestuetzte Ausfuehrung
- Explizite Runtime-Pipelines fuer `sgpt_execute`, `task_propose` und `task_execute`
- Lokale OpenAI-kompatible Backends neben LM Studio ueber gemeinsames Adaptermodell
- Persistenz via PostgreSQL (Standard) oder SQLite

Details: `docs/backend.md` und `architektur/README.md`.

## Quickstart (Docker)
1. Automatisches Setup (empfohlen):
```powershell
.\setup.ps1
```
Dieses Script prüft Dependencies (Python, Node.js, Docker), generiert .env mit sicheren Passwörtern und installiert alle Dependencies automatisch.

Alternativ manuell:
```bash
cp .env.example .env
# Bearbeiten Sie .env und ersetzen Sie alle Platzhalter-Passwörter
```

2. Start:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d
```
Windows PowerShell (bei Volume- oder Pfadfehlern):
```powershell
$env:COMPOSE_CONVERT_WINDOWS_PATHS=1
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
```
Sauberer Neustart:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
```
3. Zugriff:
- Frontend: `http://localhost:4200`
- Hub API: `http://localhost:5000`

## Entwicklung und Qualitaet
- Backend lokal: `agent/README.md`
- Frontend lokal: `frontend-angular/README.md`
- Backend-Tests: `pytest`
- Frontend E2E: `cd frontend-angular && npm run test:e2e`
- Frontend E2E gegen laufenden Docker-Stack:
  `cd frontend-angular && npm run test:e2e:compose`

Wichtige Runtime-Checks:
- `GET /providers/catalog` fuer verfuegbare Provider/Modelle inklusive `local_openai_backends`
- `GET /api/sgpt/backends` fuer CLI-Preflight, `verify_command` und lokale Runtime-Ziele
- `POST /llm/generate` fuer benchmark-basierte Modellwahl ohne explizite Provider-/Modellvorgabe

Hinweis Redis (Host-Tuning):
- Falls Redis `vm.overcommit_memory=0` meldet, unter Windows/WSL einmalig setzen:
  `wsl -d docker-desktop sysctl -w vm.overcommit_memory=1`
  Details: `docs/DOCKER_WINDOWS.md`, `docs/compose-profiles.md`

Linting:
- Backend: `python -m flake8 agent tests`
- Security-Lint (zusaetzlich in separatem CI-Job): `ruff check agent/ --select=E,F,W,S603,S607`
- Frontend: `cd frontend-angular && npm run lint`

## Weiterfuehrende Dokumentation
- `docs/INSTALL_TEST_BETRIEB.md`
- `docs/local-llm-cli-strategy.md`
- `docs/deerflow-integration.md`
- `docs/testing.md`
- `api-spec.md`
- `docs/backend.md`
- `docs/extensions.md`
- `docs/hybrid-context-pipeline.md`
- `docs/coding-conventions.md`
- `docs/e2e-mock-strategy.md`
- `docs/smart-dumb-components-guide.md`
