# Ananta

Ananta hilft dabei, groessere Entwicklungsziele in planbare Aufgaben, Ausfuehrungsschritte, Pruefung und Ergebnisse zu uebersetzen. Du beschreibst ein Ziel; der Hub plant und steuert die Arbeit, Worker fuehren delegierte Aufgaben aus.

## Schnellstart in 5 Minuten

1. Umgebung vorbereiten:
   ```powershell
   .\setup.ps1
   ```
   Das Script prueft Docker, Python und Node, legt eine `.env` an und installiert lokale Abhaengigkeiten.

2. Lite-Stack starten:
   ```bash
   docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
   ```

3. Im Browser oeffnen:
   - Frontend: `http://localhost:4200`
   - Hub API: `http://localhost:5000`

4. Einloggen:
   - Benutzer: `admin`
   - Passwort: Wert aus `INITIAL_ADMIN_PASSWORD` in `.env`

5. Erstes Ziel starten:
   - Im Dashboard ein Ziel eingeben, zum Beispiel: `Analysiere dieses Repository und schlage die naechsten Schritte vor`.
   - Alternativ zuerst die Demo-Vorschau im Dashboard ansehen.

Wenn der Browser keine Verbindung bekommt, pruefe zuerst `docker compose ps` und die Logs des Hub- und Frontend-Containers.

## Was Ananta macht

Ananta folgt einem Goal -> Plan -> Task -> Execution -> Verification -> Artifact Ablauf. Fuer den Einstieg reicht ein Ziel; Teams, Policies, Benchmarks und Expertenoptionen koennen spaeter genutzt werden.

Weitere Details und Migrationshinweise: `docs/goal-overview.md`.

## Manifest fuer verantwortliche Agentenentwicklung

Ananta versteht Agentensysteme nicht als harmlose Blackboxes, sondern als wirkungsmaechtige Systeme, die Kontrolle, Nachvollziehbarkeit, Begrenzung und ehrliche Kommunikation brauchen.

Das Projektmanifest dazu steht hier:
- `docs/responsible-agent-development-manifesto.md`

Kernaussage:
- keine Macht ohne Begrenzung
- keine Automatisierung ohne sichtbare Pruefung
- keine ernsthaften Agentensysteme ohne Verantwortung fuer reale Wirkung

## Wichtige Einstiegspunkte
- Erster Start und Betrieb: `docs/INSTALL_TEST_BETRIEB.md`, `docs/DOCKER_WINDOWS.md`
- Architektur und Zielbild: `architektur/README.md`, `docs/autonomous-platform-target-model.md`
- Backend API: `agent/README.md`, `docs/backend.md`, `api-spec.md`
- Frontend Entwicklung: `frontend-angular/README.md`
- CLI fuer Goals, Diagnose und Artefakte: `python -m agent.cli_goals --help`

## Einfache CLI- und API-Beispiele

CLI-Kurzbefehle fuer typische Einstiege:

```bash
python -m agent.cli_goals analyze "Analysiere dieses Repository"
python -m agent.cli_goals review "Pruefe die Login-Aenderungen"
python -m agent.cli_goals diagnose "Frontend erreicht den Hub nicht"
python -m agent.cli_goals patch "Plane einen kleinen Fix fuer die Validierung"
```

Minimaler API-Start:

```bash
TOKEN=$(curl -s http://localhost:5000/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"<password>"}' | jq -r '.data.access_token')
curl -s http://localhost:5000/goals -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"goal":"Analysiere dieses Repository","create_tasks":true}'
```

Weitere Beispiele stehen in `api-spec.md`.

## Welche Startvariante passt?

| Ziel | Empfohlen | Befehl |
| --- | --- | --- |
| Schnell ausprobieren oder Demo ansehen | Lite-Stack | `docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build` |
| Alltagliche lokale Nutzung | Lite-Stack mit `.env` aus `setup.ps1` | `.\setup.ps1`, dann Lite-Stack starten |
| Frontend/Backend live entwickeln | Live-Code-Stack | `scripts/compose-test-stack.sh up-live` |
| Lokale LLM-Runtime mit WSL2/Vulkan nutzen | Lite + Ollama-WSL Overlay | `docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml up -d --build` |
| Mehrere Worker-Nodes testen | Distributed Stack | `docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build` |

Neue Nutzer sollten mit dem Lite-Stack starten. Die anderen Varianten sind fuer konkrete Entwicklungs- oder Betriebsziele gedacht.

## Kanal- und Erweiterungsstrategie (Core First)

- Kernzugang zuerst: Web UI, CLI und API/Webhook sind die priorisierten Nutzflaechen.
- Externe Messaging-/Kanaladapter werden erst nach stabilem Kernzugang erweitert.
- Erweiterungen bleiben capability-gebunden und muessen Governance, Policy und Audit respektieren.
- Oekosystem-/Marktplatz-Ideen sind bewusst nachgelagert und setzen reife Kern-Contracts voraus.

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
Der kuerzeste Pfad ist der Lite-Stack aus dem Schnellstart oben. Die folgenden Varianten sind fuer Entwicklung, lokale LLM-Runtimes oder verteilte Worker gedacht.

1. Automatisches Setup:
```powershell
.\setup.ps1
```
Dieses Script prueft Dependencies, generiert `.env` mit sicheren Passwoertern und installiert alle Dependencies automatisch.

Alternativ manuell:
```bash
cp .env.example .env
# Bearbeiten Sie .env und ersetzen Sie alle Platzhalter-Passwörter
```
`.env.example` deckt die Lite-/Compose-Defaults bereits weitgehend ab; `.env.template` ist die kompaktere Vorlage, wenn Werte bewusst neu gesetzt werden sollen. Fuer die Distributed-Variante sollten zusaetzlich `AGENT_TOKEN_GAMMA`, `AGENT_TOKEN_DELTA`, `GAMMA_PORT` und `DELTA_PORT` gesetzt werden.

2. Standard-Start:
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

Live-Modus mit Quellcode-Hot-Reload und Firefox/noVNC:
```bash
scripts/compose-test-stack.sh up-live
scripts/start-firefox-vnc.sh start
```
- `up-live` aktiviert automatisch `docker-compose.live-code.yml`.
- Python-Hub/Worker nutzen dann den lokalen Quellcode per Bind-Mount (`./:/app`) und laufen mit `FLASK_DEBUG=1`.
- Das Angular-Frontend laeuft weiter mit `ng serve --poll 2000` und sieht Aenderungen in `./frontend-angular` direkt.
- noVNC: `http://localhost:7900` (Passwort im Selenium-Container: `secret`)
- Im Firefox-Container oeffnen: `http://angular-frontend:4200`
- Stoppen:
```bash
scripts/start-firefox-vnc.sh stop
scripts/compose-test-stack.sh down
```


```bashDev-Compose mit WSL2/Vulkan fuer Ollama und Live-Code-Reload fuer Python + Angular:
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.dev-vulkan-live.yml up --build
```
- Das Overlay `docker-compose.dev-vulkan-live.yml` kombiniert den bisherigen Live-Code-Modus mit dem WSL2/Vulkan-Ollama-Setup.
- Python-Container mounten den Projektcode nach `/app` und laufen mit `FLASK_DEBUG=1`, sodass Aenderungen automatisch neu geladen werden.
- Das Angular-Frontend mountet `./frontend-angular` und nutzt weiter `ng serve --poll 2000`.

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
### Check-Pipeline und Quality-Gates
Das Projekt nutzt eine vereinheitlichte Check-Pipeline fuer lokale Entwicklung und CI:
- **Standard Check:** `make check` (fuehrt Formatierung, Linting, Type-Checks, Architektur-Regeln und schnelle Tests aus)
- **Fast Check:** `make check-fast` (nur Formatierung und Linting)
- **Deep Check:** `make check-deep` (alle Checks + die gesamte Test-Suite ohne Live-Compose-Tests)
- **Formatierung:** `make format` (nutzt ruff)

**Verbindliche Quality-Gates:**
- **Pre-Push Hook:** Ein Git-Hook (`git-hooks/pre-push`) stellt sicher, dass der `Standard Check` erfolgreich durchlaeuft, bevor Code gepusht werden kann. Zur Einrichtung: `git config core.hooksPath git-hooks`.
- **CI-Enforcement:** Die GitHub Actions Pipeline führt bei jedem Push und Pull Request dieselben Checks aus. Ein Merge ist nur bei erfolgreichem `Standard Check` möglich.

### Architektur-Guardrails (BND-010)
Um Schichtverletzungen zu vermeiden, werden Import-Regeln automatisch geprueft (`scripts/check_imports.py`):
- `agent.routes` -> darf nur `services`, `common`, `models`, `auth`, `config`, `utils` importieren.
- `agent.services` -> darf nur `repositories`, `common`, `models`, `config`, `utils`, `auth` importieren.
- Direkte Importe von `repositories` in `routes` sind verboten.

### Contract-Tests (CNT-030)
Zentrale API-Schnittstellen werden durch Contract-Tests (`tests/test_api_contract_tasks.py`) gegen Regressionen geschuetzt. Diese Tests validieren die Response-Struktur gegen Pydantic-Modelle.

### Weitere Tests
- Backend lokal: `pytest`
- Frontend lokal: `cd frontend-angular && npm run lint`
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
- Template-Variablen bleiben standardmaessig warn-only; bei Bedarf kann Admin-CRUD ueber `template_variable_validation.strict=true` in `/config` oder `config.json` unbekannte `{{variablen}}` mit 4xx blockieren.
- Template-Namen sind eindeutig; API und Datenbank antworten bei Konflikten mit `409 template_name_exists`.
- Seed-Blueprints werden beim Lesen deterministisch reconciled; referenzierte Blueprints koennen nicht geloescht werden und antworten mit `409 blueprint_in_use`.
- Admin-/Rollout-Details fuer Blueprints und Templates: `docs/blueprint-admin.md`, `docs/blueprint-migration-rollout.md`

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
- `docs/ollama-model-routing.md`
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
- `docs/blueprint-admin.md`
- `docs/blueprint-migration-rollout.md`
- `docs/responsible-agent-development-manifesto.md`
