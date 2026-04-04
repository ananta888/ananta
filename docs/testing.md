# Testing Guide

## Lite Compose Standard (E2E)

Standard for local E2E runs is the existing lite docker environment:

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
cd frontend-angular
npm run test:e2e:lite
```

WSL2/Vulkan fuer den Compose-Ollama-Service:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml up -d --build
cd frontend-angular
npm run test:e2e:lite
```

Alternative mit bestehendem Compose-Stack und kompakter Ausgabe:
```bash
cd frontend-angular
npm run test:e2e:compose
```

## Compose-Standard mit Ollama

Die standardisierte Testwelt ist jetzt die Compose-Umgebung mit Hub, Workern, Frontend und `ollama` als lokalem LLM-Service.

Start:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml up -d --build
```

Alternative ohne WSL2/Vulkan-Overlay:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml up -d --build
```

Wichtig fuer lokale Browser-Nutzung:
- Der Test-Overlay `docker-compose.test.yml` setzt fuer Services wie `angular-frontend` explizit `ports: !reset []`.
- Damit sind Frontend/Hub/Worker im Test-Stack fuer den Host absichtlich nicht direkt ueber `localhost:*` veroeffentlicht.
- Fuer Browser-Zugriff wieder ohne Test-Overlay starten:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
```

Backend:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml run --rm backend-test
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml run --rm backend-live-llm-test
```

Standard fuer `backend-live-llm-test`:
- Provider: `ollama`
- Modell: `ananta-smoke`
- Timeout fuer Live-Planer: `60s`
- Uebersteuerbar per `LIVE_LLM_MODEL`, `LIVE_LLM_TIMEOUT_SEC`, `LIVE_LLM_RETRY_ATTEMPTS`, `LIVE_LLM_RETRY_BACKOFF_SEC`

Alternative ohne WSL2/Vulkan-Overlay:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml run --rm backend-test
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml run --rm backend-live-llm-test
```

Frontend:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml run --rm frontend-test
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml run --rm frontend-live-llm-test
```

Live-Agent-Chain ohne Mock:
```bash
env RUN_LIVE_LLM_TESTS=1 RUN_LIVE_AGENT_CHAIN_E2E=1 .venv/bin/pytest -q tests/test_live_agent_chain_e2e.py -rs
```

Wichtige Live-Flags:
- `RUN_LIVE_AGENT_CHAIN_E2E=1` aktiviert den echten Agent-Chain-Test.
- `LIVE_LLM_PROVIDER=ollama` ist der Standard fuer den Test.
- `E2E_OLLAMA_URL` kann explizit gesetzt werden, falls `ollama` nicht unter dem Docker-DNS-Namen erreichbar ist.

URL-Reihenfolge fuer Ollama im Live-Agent-Chain-Test:
- `OLLAMA_URL`
- `E2E_OLLAMA_URL`
- `http://ollama:11434/api/generate`
- `http://localhost:11434/api/generate`
- `http://127.0.0.1:11434/api/generate`
- `http://host.docker.internal:11434/api/generate`

Die uebergreifende Compose-/Host-/WSL-Matrix steht in [container-networking-matrix.md](/mnt/c/Users/pst/IdeaProjects/ananta/docs/container-networking-matrix.md).

Alternative ohne WSL2/Vulkan-Overlay:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml run --rm frontend-test
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml run --rm frontend-live-llm-test
```

Stop:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml down -v --remove-orphans
```

Alternative ohne WSL2/Vulkan-Overlay:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml down -v --remove-orphans
```

## Windows + WSL2 (Docker in Linux-Distro)

Wenn Docker direkt in Ihrer WSL2-Distro laeuft (nicht ueber Docker Desktop), kann `http://localhost:4200` auf Windows trotz aktivem Port-Mapping nicht automatisch funktionieren.

Optionen:
- Direkter Zugriff ueber WSL-IP: `http://<wsl-ip>:4200`
- Oder `portproxy` auf Windows einrichten (Administrator-PowerShell), damit `localhost:4200` nach WSL weitergeleitet wird.

Vorbereitetes Setup im Projekt:
- `setup_host_services.ps1` richtet Firewall und `netsh interface portproxy` bereits fuer relevante Ports inkl. `4200` ein.

Pragmatischer Weg fuer laufende Test-Instanz:
- Starten Sie einen Browser-Container im gleichen Docker-Netz wie die Test-Services:
```bash
scripts/start-firefox-vnc.sh start
```
- Host-Zugriff: `http://localhost:7900`
- Im Browser-Container: `http://angular-frontend:4200`
- Stoppen:
```bash
scripts/start-firefox-vnc.sh stop
```

Dauerhafte Windows-`localhost`-Weiterleitung nach WSL:
- Administrator-PowerShell:
```powershell
.\setup_wsl_localhost_portproxy.ps1 -Distro Ubuntu -Ports 4200,7900
```
- Entfernen der Regeln:
```powershell
.\setup_wsl_localhost_portproxy.ps1 -Distro Ubuntu -Ports 4200,7900 -RemoveOnly
```
- Hinweis: Nach `wsl --shutdown` kann sich die WSL-IP aendern, dann Skript erneut ausfuehren.

Optional fuer lange lokale Laeufe:

```powershell
$env:E2E_LITE_TIMEOUT_MINUTES="35"
npm run test:e2e:lite
```

Artifacts:
- `frontend-angular/test-results/junit-results.xml`
- `frontend-angular/test-results/results.json`
- `frontend-angular/test-results/failure-summary.md`

`test:e2e:lite` gibt zusaetzlich bei Fehlern eine kurze Failure-Summary aus `results.json` aus.

## Mocking-Standard fuer E2E
- Richtlinien und Beispiele: `docs/e2e-mock-strategy.md`

## Bekannte Probleme

### Windows Docker Hot-Reload Caching
In Windows-Umgebungen mit Docker Desktop kann es vorkommen, dass Änderungen im Quellcode zwar in das Volume gespiegelt werden, der Angular Dev-Server (im Container) aber veraltete JS-Bundles aus seinem Cache ausliefert. Dies führt oft zu scheinbar zufälligen Fehlern in E2E-Tests.

**Symptome:**
- Tests schlagen fehl mit "Fehler bei Ausführung", obwohl der Code korrekt aussieht.
- Änderungen am Frontend werden im Browser erst nach mehrmaligem Neuladen oder gar nicht sichtbar.

**Lösung / Workaround:**
1. **Container neu bauen:** Führen Sie `docker-compose up -d --build` aus, um den Cache zu erzwingen.
2. **Browser-Cache leeren:** In manchen Fällen hilft auch ein Hard-Reload (Strg + F5) oder ein privates Fenster.
3. **Produktions-Build:** Vor dem Start der Container `npm run build` im `frontend-angular` Verzeichnis ausführen.

## Flaky Tests
Einige Tests sind als `@flaky` markiert. Diese sollten in CI-Umgebungen mit speziellen Einstellungen (z.B. `--retries`) ausgeführt werden.

### Liste der Flaky Tests
1. `execute manual command on worker` in `frontend-angular/tests/agents.spec.ts`
   - Ursache: Hot-Reload Caching Problem (siehe oben).

## Test-Reports und Coverage

Das Projekt generiert automatisierte Test-Reports in der CI-Pipeline:

### Backend (pytest)
- **JUnit XML:** `test-reports/backend-junit.xml`
- **Coverage XML:** `test-reports/backend-coverage.xml`

Lokal können diese Reports wie folgt generiert werden (erfordert `pytest-cov`):
```bash
mkdir -p test-reports
pytest --junitxml=test-reports/backend-junit.xml --cov=agent --cov-report=xml:test-reports/backend-coverage.xml
```

### Frontend (Playwright)
- **JUnit XML:** `frontend-angular/test-results/junit-results.xml`

Der Report wird automatisch bei jedem E2E-Testlauf (`npm run test:e2e`) erstellt.

## Hintergrund-Threads deaktivieren
Standardmaessig werden Hintergrund-Threads in pytest-Laeufen automatisch deaktiviert.
Zusaetzlich steht ein expliziter Env-Override zur Verfuegung:

```bash
ANANTA_DISABLE_BACKGROUND_THREADS=1
```

Damit lassen sich lokale Diagnose- oder CI-Sonderfaelle reproduzierbar ohne
Monitoring/Housekeeping/Registrierungs-Threads ausfuehren.

## Lokale LLM-Preflight-Pruefung

Vor Live-E2E oder lokalen CLI-Tests unter Windows:

```powershell
.\setup_host_services.ps1
```

Der Lauf prueft jetzt zusaetzlich:

- Erreichbarkeit des konfigurierten lokalen LLM-Backends in der Docker-/Compose-Welt dieser Windows-11-WSL2-Umgebung
- Agent-Health auf `http://127.0.0.1:5000/health`, `5001/health`, `5002/health`
- Verfuegbarkeit der optionalen CLI-Binaries `codex`, `opencode`, `aider`, `mistral-code`
- Konsistenz der Runtime-Diagnostik in `/api/sgpt/backends` inklusive `verify_command`

Danach sollten im Agenten auch `/api/sgpt/backends` und `/ready` plausibel sein.

## Runtime-Profile Validierung

Gezielte Checks fuer Runtime-Profile:

```bash
.venv/bin/pytest -q tests/test_config_api.py -k runtime_profile
.venv/bin/pytest -q tests/test_system_health_standard.py -k runtime_profile
```

Erwartung:

- `/config` liefert `runtime_profile_effective`.
- `/health` liefert `checks.runtime_profile`.
- Ungueltige Werte fuer `runtime_profile` werden via `POST /config` mit `400 invalid_runtime_profile` abgelehnt.
