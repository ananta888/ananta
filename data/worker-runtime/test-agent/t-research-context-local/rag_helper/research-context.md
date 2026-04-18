Artefakt-Kontext:
- Artifact 4d87820d-57a5-4923-b16c-96f1146e4854 (README.md):
# Hello
artifact body

Knowledge-Kontext:
- Collection research-docs:
  - README.md: knowledge chunk about retries

Repo-Kontext:
- Repo-Scope README.md: file
# Ananta

Modulares Multi-Agent-System fuer AI-gestuetzte Entwicklung mit Hub-Worker-Architektur.

## Goal-basierter Produktansatz
Das System priorisiert jetzt einen Goal->Plan->Task->Execution->Verification->Artifact-Workflow. Für einfache Erstbenutzung ist nur ein Goal notwendig; Persistenz und erweiterte Optionen sind konfigurierbar. Weitere Details und Migrationshinweise: `docs/goal-overview.md`.

## Einstiegspunkte
- Architektur und Zielbild: `architektur/README.md`, `docs/autonomous-platform-target-model.md`
- Backend API und Betrieb: `agent/README.md`, `docs/backend.md`, `api-spec.md`
- Frontend Entwicklung und E2E: `frontend-angular/README.md`
- Setup/Runtime: `docs/INSTALL_TEST_BETRIEB.md`, `docs/DOCKER_WINDOWS.md`

## Architektur
- Angular Frontend fuer Visualisierung und Steuerung
- Hub-Agent fuer Orchestrierung (Tasks, Teams, Templates)
- Team-Konfiguration blueprint-first: wiederverwendbare Blueprints, Team-Instanzen und Advanced-Verwaltung
- Worker-Agenten fuer LLM-gestuetzte Ausfuehrung
- Explizite Runtime-Pipelines fuer `sgpt_execute`, `task_propose` und `task_execute`
- Lokale OpenAI-kompatible Backends wie Ollama oder LM Studio ueber gemeinsames Adaptermodell
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
`.env.example` deckt die Lite-/Compose-Defaults bereits weitgehend ab; `.env.template` ist die kompaktere Vorlage, wenn Werte bewusst neu gesetzt werden sollen. Fuer die Distributed-Variante sollten zusaetzlich `AGENT_TOKEN_GAMMA`, `AGENT_TOKEN_DELTA`, `GAMMA_PORT` und `DELTA_PORT` gesetzt werden.

2. Start:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d
```
WSL2 mit AMD/Vulkan fuer den Compose-Ollama-Service:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml up -d --build
```
Distributed mit zusaetzlichen Worker-Nodes:
```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build
```
Windows PowerShell (bei Volume- oder Pfadfehlern):
```powershell
$env:COMPOSE_CONVERT_WINDOWS_PATHS=1
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
```
Sauberer Neustart:
```bash
scripts/compose-test-stack.sh down
scripts/compose-test-stack.sh up
```
Mit WSL2/Vulkan-Overlay:
```bash
scripts/compose-test-stack.sh down
scripts/compose-test-stack.sh up
```
Sicheres Cleanup (inkl. Volumes ausser `ollama_data`):
```bash
scripts/compose-test-stack.sh clean
```
3. Zugriff:
- Frontend: `http://localhost:4200`
- Hub API: `http://localhost:5000`

Hinweis z...
