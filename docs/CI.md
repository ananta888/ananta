# CI-Testmatrix und Pipeline-Strategie

Dieses Dokument beschreibt die Continuous Integration (CI) Strategie für Ananta, wie sie in `.github/workflows/ci.yml` implementiert ist.

## Pipeline-Struktur

Die CI-Pipeline besteht aus folgenden Jobs:

1.  **lint**: Prüft den Code-Stil des Backends mittels `flake8`.
2.  **backend-tests**: Führt die Python-Unit-Tests in einem Docker-Container aus.
3.  **frontend-tests**: Führt Playwright-E2E-Tests für das Angular-Frontend in einem Docker-Container aus.
4.  **frontend-live-llm-tests**: Führt E2E-Tests mit einem Mock-LMStudio-Server (ebenfalls containerisiert) durch.
5.  **docker-build**: Validiert, ob das Haupt-Image fehlerfrei gebaut werden kann.

## Skip-Regeln und Trigger

### Standard-Tests
Alle Tests außer `frontend-live-llm-tests` laufen bei jedem `push` und `pull_request`.

### Live-LLM Tests (Opt-in)
Der Job `frontend-live-llm-tests` ist zeitaufwendig und ressourcenintensiv. Er wird daher nur unter folgenden Bedingungen ausgeführt:
-   **Workflow Dispatch**: Manueller Start über das GitHub Actions UI.
-   **Schedule**: Einmal wöchentlich (montags um 03:00 Uhr).

### Caching
Um die Pipeline zu beschleunigen, nutzen wir Caching für:
-   **pip**: Python-Abhängigkeiten.
-   **npm**: Node.js-Abhängigkeiten.
-   **Playwright**: Browser-Binaries.
-   **Docker Layers**: Beschleunigt den `docker-build` Job.

## Lokale Reproduktion

Um die CI-Schritte lokal über Docker Compose zu testen:

### Backend-Tests
```bash
docker compose -f docker-compose.test.yml run --rm backend-test
```

### Frontend-Tests
```bash
docker compose -f docker-compose.test.yml run --rm frontend-test
```

### Mock-LLM-Test lokal simulieren
```bash
docker compose -f docker-compose.test.yml run --rm frontend-live-llm-test
```
