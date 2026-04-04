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

Hinweis zum Test-Compose-Stack:
- `docker-compose.test.yml` entfernt Host-Port-Mappings absichtlich (`ports: !reset []`), damit Tests intern ueber Compose-DNS laufen.
- Wenn Sie danach wieder lokal im Browser auf `http://localhost:4200` zugreifen wollen, starten Sie den Frontend-Service wieder mit `docker-compose.base.yml` + `docker-compose-lite.yml` (ohne `docker-compose.test.yml`).
- Bei Docker in einer nativen WSL2-Distro (ohne Docker-Desktop-Loopback) kann unter Windows zusaetzlich ein `portproxy` noetig sein. Dafuer ist `setup_host_services.ps1` vorbereitet (inkl. Port `4200`).

Frontend waehrend laufender Test-Instanz ansehen (empfohlen):
- Browser-in-Container im gleichen Compose-Netz starten:
  `scripts/start-firefox-vnc.sh start`
- Dann auf dem Host oeffnen: `http://localhost:7900`
- Im Firefox-Container aufrufen: `http://angular-frontend:4200`

Alternative fuer dauerhaftes Windows-`localhost` (WSL2 ohne Docker Desktop):
- Administrator-PowerShell:
  `.\setup_wsl_localhost_portproxy.ps1 -Distro Ubuntu -Ports 4200,7900`
- Danach sind `http://localhost:4200` und `http://localhost:7900` nach WSL weitergeleitet.

## Entwicklung und Qualitaet
- Backend lokal: `agent/README.md`
- Frontend lokal: `frontend-angular/README.md`
- Backend-Tests: `pytest`
- Frontend E2E: `cd frontend-angular && npm run test:e2e`
- Frontend E2E gegen laufenden Docker-Stack:
  `cd frontend-angular && npm run test:e2e:compose`
- Standard fuer Live-LLM-Tests:
  `docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml run --rm frontend-live-llm-test`
- Alternative ohne WSL2/Vulkan-Overlay:
  `docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml run --rm frontend-live-llm-test`
- Die wichtigsten Test-/Overlay-Variablen (`RUN_LIVE_LLM_TESTS`, `LIVE_LLM_MODEL`, `LIVE_LLM_TIMEOUT_SEC`, `E2E_OLLAMA_URL`, `E2E_LMSTUDIO_URL`, `E2E_ADMIN_PASSWORD`) sind jetzt ebenfalls in `.env.example` und `.env.template` vorgemerkt.
- Live-Backend-Tests gegen Ollama nutzen standardmaessig das schnellere Modell `ananta-smoke`; Timeout/Modell bleiben per Compose-Env uebersteuerbar.
- Echten Agent-Chain-Live-Test ohne Mock starten:
  `env RUN_LIVE_LLM_TESTS=1 RUN_LIVE_AGENT_CHAIN_E2E=1 .venv/bin/pytest -q tests/test_live_agent_chain_e2e.py -rs`
- Falls `ollama` aus der Shell nicht per Docker-DNS aufloesbar ist, nutzt der Live-Agent-Chain-Test nacheinander `OLLAMA_URL`, `E2E_OLLAMA_URL`, `http://ollama:11434`, `http://localhost:11434`, `http://127.0.0.1:11434` und `http://host.docker.internal:11434`.
- Die konsolidierte Matrix fuer Compose-/Host-/WSL-Erreichbarkeit steht in [docs/container-networking-matrix.md](/mnt/c/Users/pst/IdeaProjects/ananta/docs/container-networking-matrix.md).

Wichtige Runtime-Checks:
- `GET /providers/catalog` fuer verfuegbare Provider/Modelle inklusive `local_openai_backends`
- `GET /api/sgpt/backends` fuer CLI-Preflight, `verify_command` und lokale Runtime-Ziele
- `POST /llm/generate` fuer benchmark-basierte Modellwahl ohne explizite Provider-/Modellvorgabe
- `GET /v1/ananta/capabilities` fuer aktive OpenAI-Compat-Funktionen und effektive Exposure-Policy
- OpenAI-Compat Self-Loop-/Hop-Guards basieren auf `X-Ananta-Instance-ID` und `X-Ananta-Hop-Count`

Wichtige Security-Policy:
- OpenAI-Compat und zukuenftige MCP-Exposition werden ueber `exposure_policy` in `/config` explizit gesteuert.
- Remote-Hub-Ziele koennen additiv ueber `remote_ananta_backends` konfiguriert werden und sind im Provider-Katalog sichtbar.

Hinweis Redis (Host-Tuning):
- Falls Redis `vm.overcommit_memory=0` meldet, unter Windows/WSL einmalig setzen:
  `wsl -d docker-desktop sysctl -w vm.overcommit_memory=1`
  Details: `docs/DOCKER_WINDOWS.md`, `docs/compose-profiles.md`

Hinweis Ollama unter WSL2/Vulkan:
- Das optionale Overlay `docker-compose.ollama-wsl.yml` erweitert nur den `ollama`-Service und laesst Hub/Worker unveraendert.
- Voraussetzung ist WSL2 mit verfuegbarem `/dev/dxg`; das Overlay bindet zusaetzlich `/usr/lib/wsl` read-only in den Container ein.
- Fuer E2E-/Klicktests ist dieser Weg jetzt Standard ueber `scripts/compose-test-stack.sh` (Overlay standardmaessig aktiv, Opt-out via `ANANTA_USE_WSL_VULKAN=0`).
- `scripts/compose-test-stack.sh clean` loescht bewusst **nicht** das Volume `ollama_data` (LLM-Modelle bleiben erhalten).

Linting:
- Backend: `python -m flake8 agent tests`
- Security-Lint (zusaetzlich in separatem CI-Job): `ruff check agent/ --select=E,F,W,S603,S607`
- Frontend: `cd frontend-angular && npm run lint`

## Weiterfuehrende Dokumentation
- `docs/INSTALL_TEST_BETRIEB.md`
- `docs/local-llm-cli-strategy.md`
- `docs/deerflow-integration.md`
- `docs/testing.md`
- `README_VERGLEICHSPROJEKTE.md`
- `api-spec.md`
- `docs/backend.md`
- `docs/extensions.md`
- `docs/hybrid-context-pipeline.md`
- `docs/coding-conventions.md`
- `docs/e2e-mock-strategy.md`
- `docs/smart-dumb-components-guide.md`
