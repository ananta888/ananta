# Testing Guide

## Lite Compose Standard (E2E)

Standard for local E2E runs is the existing lite docker environment:

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml down -v --remove-orphans
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
cd frontend-angular
npm run test:e2e:lite
```

Optional fuer lange lokale Laeufe:

```powershell
$env:E2E_LITE_TIMEOUT_MINUTES="35"
npm run test:e2e:lite
```

Artifacts:
- `frontend-angular/test-results/junit-results.xml`
- `frontend-angular/test-results/results.json`

`test:e2e:lite` gibt zusaetzlich bei Fehlern eine kurze Failure-Summary aus `results.json` aus.

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
