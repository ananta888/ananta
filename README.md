# Ananta

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Persistente Daten wie Konfigurationen, Aufgaben, Logs und Steuerflags werden in PostgreSQL gespeichert.

## High-Level Objectives

- Document core architecture and establish coding conventions.
- Deliver a usable dashboard with environment setup guidance.
- Automate testing and deployment workflows.
- Enable modular extensions for additional agent roles.

Weitere Details siehe [Product Roadmap](docs/roadmap.md) und [README-TESTS.md](README-TESTS.md) für Testhinweise.

## Quickstart

```bash
# Container starten
docker-compose up -d

# Logs ansehen
docker-compose logs -f
```

## Struktur

- `agent/` – AI-Agent-Code
- `controller/` – Controller-Implementierung
- `frontend/` – Vue-Frontend (wird im Docker-Build kompiliert)
- `architektur/` – Dokumentation der Systemarchitektur
- [src/](src/README.md) – Backend-Quellcode und Hilfsmodule
- `tasks_history/` – Aufgabenhistorie pro Rolle

## Komponenten

### Controller (Flask-Server)
- Verwaltet Konfiguration, Aufgabenliste, Blacklist und Log-Export über PostgreSQL.
- Stellt HTTP-Endpunkte für Agenten, Dashboard und das gebaute Vue-Frontend bereit.

### AI-Agent (`agent/ai_agent.py`)
- Pollt den Controller, rendert Prompts aus Templates und führt bestätigte Kommandos aus.
- HTTP-Aufrufe erfolgen über das gemeinsame Modul `common/http_client.py` mit Retry/Timeout.
- Stellt eigene Routen (`/health`, `/logs`, `/tasks`, `/stop`, `/restart`) bereit.
- Nutzt `ModelPool`, um gleichzeitige Modellanfragen pro Provider/Modell zu begrenzen.
- Schreibt eigene Einstellungen und Laufzeit-Logs in das Schema `agent` der Datenbank.
- Speichert Aufgabenverläufe in `tasks_history/<rolle>.json` (JSON-Array mit `task` und `date`).

### Frontend (`frontend/`)
- Vue-Dashboard zur Anzeige von Logs und Steuerung der Agenten.
- Kommuniziert über Fetch-Aufrufe mit dem Controller.

## Wichtige Module

| Pfad | Zweck |
| ---- | ----- |
| `src/agents/` | Agent-Dataclass und Prompt-Template-Utilities. |
| `src/controller/` | `ControllerAgent` und zusätzliche HTTP-Routen. |
| `src/models/pool.py` | `ModelPool` zur Limitierung paralleler LLM-Anfragen. |
| `common/http_client.py` | HTTP-Hilfsfunktionen für Agent und Controller. |
| `agent/ai_agent.py` | Hilfsfunktionen und Hauptschleife des Agents. |
| `controller/controller.py` | Datenbankgestützte Konfigurationsverwaltung und HTTP-Endpoints. |

## HTTP-Endpunkte

Beispiele für Controller-Endpoints (alle Antworten sind JSON):
# E2E-Test Optimierung

Dieses Dokument erklärt, wie die End-to-End-Tests mit Playwright in Docker optimiert werden können.

## Optimierungsstrategien

1. **Nutzung eines einzelnen Browsers**: Anstatt alle Browser zu testen, beschränken wir uns auf Chromium für schnellere Tests.

   ```bash
   export PLAYWRIGHT_BROWSER=chromium
   ```

2. **Browser-Caching**: Browser-Binärdateien werden gecacht, um wiederholte Downloads zu vermeiden.

   ```bash
   # In docker-compose.yml hinzufügen
   volumes:
     - ./.playwright-cache:/app/.playwright-cache
   ```

3. **Container-Optimierung**: Spezielle Browser-Argumente für Ausführung in Docker.

4. **Parallelisierung**: Tests werden parallel ausgeführt, aber im CI mit weniger Workern.

## Verwendung

```bash
# Optimierte Tests ausführen
docker-compose exec app ./docker/speedup-e2e.sh

# Spezifische Tests ausführen
docker-compose exec app ./docker/speedup-e2e.sh tests/e2e/login.spec.js
```

## CI-Integration

Für CI-Pipelines empfehlen wir:

1. Caching der `.playwright-cache`-Verzeichnisse zwischen Builds
2. Verwendung eines einzelnen Browsers (Chromium)
3. Reduzierte Parallelisierung mit `workers: 1` im CI

## Bekannte Einschränkungen

- Browserübergreifende Tests werden nicht durchgeführt
- Video/Screenshot-Erfassung nur bei Fehlern aktiviert
- POST /agent/add_task
  Body: {"task": "hello", "agent": "alice", "template": "basic"}
  Response: {"status": "queued"}

- GET /controller/next-task
  Response: {"task": "hello"} oder {"task": null}

- POST /controller/blacklist
  Body: {"task": "rm -rf /"}
  Response: {"status": "added"} oder {"status": "exists"}

- GET /config
  Response: {"api_endpoints": [], "agents": {}, "templates": {}}

- POST /config/api_endpoints
  Body: {"api_endpoints": ["/a", "/b"]}
  Response: {"status": "ok"}

- POST /approve
  Body: {"result": "ok"}
  Response: {"status": "approved"}

- GET /agent/<name>/log?limit=100
  Response: [{"agent": "alice", "level": 20, "message": "hi"}, ...]

- DELETE /agent/<name>/log
  Response: {"status": "deleted"}

- POST /agent/<name>/toggle_active
  Response: {"active": false}

- GET /agent/<name>/tasks
  Response: {"tasks": ["t1", "t2"]}

- GET /next-config
  Response: {"tasks": ["t1"], "templates": {}}

Sicherheit:
- Eingaben werden validiert (Typen, Längenbeschränkungen, Paginierung).
- SQLAlchemy ORM verhindert SQL-Injection; DB-Spalten sind indiziert, wo sinnvoll.
- Sicherheits-Header werden über @after_request gesetzt.

## Ablauf

1. **Startup** – Beim ersten Start wird die Datenbank initialisiert und mit den Daten aus `config.json` befüllt.
2. **Agentenlauf** – `agent/ai_agent.py` pollt `/next-config` und `/tasks/next`, erstellt Prompts und ruft LLMs auf; Ergebnisse werden über `/approve` zurückgemeldet. Über die Flags `/stop` und `/restart` kann der Controller den Loop steuern.
3. **Dashboard** – Vue-UI und HTML-Views nutzen Controller-Endpunkte, um Statusinformationen aus der Datenbank anzuzeigen und Eingriffe zu ermöglichen.

## Persistenz der Konfiguration

- Alle Konfigurations- und Logdaten liegen in PostgreSQL. Die Datei `config.json` dient nur als Vorlage für die initiale Befüllung.
- Die Verbindung wird über die Umgebungsvariable `DATABASE_URL` konfiguriert. Docker Compose startet automatisch einen PostgreSQL-Container und setzt diese Variable für Controller und Agent.

## Erweiterbarkeit

- Zusätzliche Agenten über SQL-Inserts in die Config verwalten.
- Neue Prompt-Templates in der Datenbank registrieren.
- `ModelPool` erlaubt konfigurationsabhängiges Throttling pro Provider/Modell.

## Tests

- Python-Tests: `python -m unittest`
- Playwright-E2E-Tests: `npm test`
- siehe README-TESTS.md für Docker-Anweisungen und die Variable `RUN_TESTS`.
- Linting: `flake8 .` für Python, `npm --prefix frontend run lint` für das Dashboard.

## Fehlersuche

Falls Fehler beim Starten der Container auftreten, überprüfen Sie:

1. Sind alle benötigten Dateien vorhanden (insbesondere im Pfad `agent/ai_agent.py`)?
2. Stimmen die Pfade im Dockerfile mit der tatsächlichen Projektstruktur überein?
3. Sind die Umgebungsvariablen korrekt gesetzt?

Siehe auch die README-Dateien in den jeweiligen Unterverzeichnissen für mehr Details.

## Weitere Dokumentation

- [frontend/README.md](frontend/README.md) – Nutzung des Vue-Dashboards.
- [docs/roadmap.md](docs/roadmap.md) – Produkt-Roadmap und Ziele.

## Security Headers

Stellen Sie sicher, dass HTTP-Antworten Sicherheits-Header wie
`Content-Security-Policy`, `X-Frame-Options`, `Referrer-Policy` und
`Strict-Transport-Security` setzen, um gängige Angriffe zu vermeiden.
