# Ananta

Aktualisierung (Dez 2025): Die Architektur wurde vereinfacht. Es gibt jetzt nur noch zwei Komponenten:
- Angular Single Page App unter `frontend-angular/`
- Mehrere unabhängige `ai_agent.py`-Instanzen (Flask‑APIs), optional mit Hub‑Rolle (`ROLE=hub`) für Tasks/Templates/Board

Der frühere Controller, die Datenbank und das alte Vue‑Frontend wurden entfernt.

Quickstart (neu):
```
docker-compose up -d

# Frontend: http://localhost:4200
# Hub:      http://localhost:5000
# Worker:   http://localhost:5001, http://localhost:5002
```

Siehe außerdem `frontend-angular/README.md` für lokale Entwicklung.

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

Controller- und Agent-Endpoints (alle Antworten JSON, sofern nicht anders angegeben):
Hinweis: DB-gestützte Zusatzrouten unter `/controller/*` sind nur aktiv, wenn `ENABLE_DB_ROUTES=1` gesetzt ist.
- GET /health
  Response: {"status": "ok"}

- GET /status
  Response: {"status": "ok"}

- GET /
  Redirect: "/ui/"

- GET /ui/ und /ui
  Liefert index.html (sofern gebaut) oder 404 {"error": "ui_not_built"}

- GET /ui/<path>
  Statische Assets; SPA-Fallback auf index.html

- POST /agent/add_task
  Body: {"task": "hello", "agent": "alice", "template": "basic"}
  Response: {"status": "queued"}

- GET /controller/next-task
  Response: {"task": "hello"} oder {"task": null}

- POST /controller/blacklist
  Body: {"task": "rm -rf /"}
  Response: {"status": "added"} oder {"status": "exists"}

- GET /config
  Response: {"api_endpoints": [], "agents": {}, "prompt_templates": {}}

- POST /config/api_endpoints
  Body: {"api_endpoints": ["/a", "/b"]}
  Response: {"status": "ok"}

- POST /approve
  Body: {"result": "ok"}
  Response: {"status": "approved"}

- GET /agent/<name>/log?limit=100
  Response: [{"agent": "alice", "level": "INFO", "message": "hi", "timestamp": "2025-09-01T12:00:00Z"}, ...]

- DELETE /agent/<name>/log
  Response: {"status": "deleted"}

- POST /agent/<name>/toggle_active
  Response: {"active": false}

- GET /agent/<name>/tasks
  Response: {"tasks": [{"id": 1, "task": "t1", "agent": "alice", "template": null}]}

- GET /tasks/next?agent=<name>
  Response: {"task": "t1"} oder {"task": null}

- GET /next-config
  Response: {"agent": null, "api_endpoints": [], "prompt_templates": {}}

Sicherheit:
- Eingaben werden validiert (Typen, Längenbeschränkungen, Paginierung).
- SQLAlchemy ORM verhindert SQL-Injection; DB-Spalten sind indiziert, wo sinnvoll.
- Sicherheits-Header werden über @after_request gesetzt.

## Ablauf

1. **Startup** – Beim ersten Start wird die Datenbank initialisiert und mit den Daten aus `config.json` befüllt.
2. **Agentenlauf** – `agent/ai_agent.py` pollt `/tasks/next`, erstellt Prompts und ruft LLMs auf; Ergebnisse werden über `/approve` zurückgemeldet. Über die Flags `/stop` und `/restart` kann der Controller den Loop steuern.
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
- [docs/task-flow-de.md](docs/task-flow-de.md) – Aufgabenfluss von Frontend über Controller zum AI‑Agent.

## Security Headers

Stellen Sie sicher, dass HTTP-Antworten Sicherheits-Header wie
`Content-Security-Policy`, `X-Frame-Options`, `Referrer-Policy` und
`Strict-Transport-Security` setzen, um gängige Angriffe zu vermeiden.
