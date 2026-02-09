# CI-Testmatrix und Pipeline-Strategie

Dieses Dokument beschreibt die Continuous Integration (CI) Strategie für Ananta, wie sie in `.github/workflows/ci.yml` implementiert ist.

## Pipeline-Struktur

Die CI-Pipeline besteht aus folgenden Jobs:

1.  **lint**: Prüft den Code-Stil des Backends mittels `flake8`.
2.  **backend-tests**: Führt die Python-Unit-Tests aus.
3.  **frontend-tests**: Installiert Abhängigkeiten, Playwright-Browser und führt E2E-Tests für das Angular-Frontend aus.
4.  **frontend-live-llm-tests**: Ein spezieller Job, der einen Mock-LMStudio-Server startet und E2E-Tests mit simulierter LLM-Antwort durchführt.
5.  **docker-build**: Validiert, ob das Docker-Image fehlerfrei gebaut werden kann.

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

Um die CI-Schritte lokal zu testen:

### Backend-Tests
```bash
python -m unittest discover tests
```

### Frontend-Tests
```bash
cd frontend-angular
npm ci
npx playwright install --with-deps
npm run test:e2e
```

### Mock-LLM-Test lokal simulieren
1.  Mock-Server starten:
    ```bash
    python devtools/mock_lmstudio_server.py --host 127.0.0.1 --port 1234
    ```
2.  Tests mit Umgebungsvariablen starten:
    ```bash
    cd frontend-angular
    export RUN_LIVE_LLM_TESTS=1
    export LMSTUDIO_URL=http://127.0.0.1:1234/v1
    export DISABLE_LLM_CHECK=1
    npm run test:e2e:live
    ```
