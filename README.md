# Ananta

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Persistente Daten wie Konfigurationen, Aufgaben, Logs und Steuerflags werden in PostgreSQL gespeichert.

## High-Level Objectives

- Document core architecture and establish coding conventions.
- Deliver a usable dashboard with environment setup guidance.
- Automate testing and deployment workflows.
- Enable modular extensions for additional agent roles.

Weitere Details siehe [Product Roadmap](docs/roadmap.md).
- [README-TESTS.md](README-TESTS.md) – Playwright environment & Docker usage.

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

## Fehlersuche

Falls Fehler beim Starten der Container auftreten, überprüfen Sie:

1. Sind alle benötigten Dateien vorhanden (insbesondere im Pfad `agent/ai_agent.py`)?
2. Stimmen die Pfade im Dockerfile mit der tatsächlichen Projektstruktur überein?
3. Sind die Umgebungsvariablen korrekt gesetzt?

Siehe auch die README-Dateien in den jeweiligen Unterverzeichnissen für mehr Details.

## Weitere Dokumentation

- [frontend/README.md](frontend/README.md) – Nutzung des Vue-Dashboards.
- [docs/roadmap.md](docs/roadmap.md) – Produkt-Roadmap und Ziele.
- [README-TESTS.md](README-TESTS.md) – Playwright environment & Docker usage.


## Todo-Verarbeitung

Das Skript `tools\\process_todos.py` verarbeitet Aufgaben gemäß der Vorgabe:

- Liest `config.json` und nutzt die dortigen `prompt_templates` pro Rolle.
- Liest `todo.json` und `todo_next.json` und wendet das passende Prompt auf die jeweilige Rolle+Aufgabe an (für Nachvollziehbarkeit werden die gerenderten Prompts im Terminal ausgegeben).
- Arbeitet alle Tasks ab und hängt sie an die Historie unter `tasks_history/<rolle>.json` an (nicht-destruktiv, mit Zeitstempel).
- Erzeugt sinnvolle Folgeaufgaben pro Rolle und speichert sie ebenfalls in der jeweiligen Historie (die ursprünglichen Aufgaben bleiben erhalten).
- Falls eine Aufgabe nicht abgeschlossen werden kann (z. B. fehlendes Template), wird unter `tasks_history/pending/` eine eigene Task-Datei mit beschreibendem Dateinamen angelegt und notwendige Subtasks entsprechenden Agenten zugeordnet.

Rollen-Mapping (Beispiele aus `todo.json` → `config.json`):
- `architect` → `Architect`
- `back-end developer` → `Backend Developer`
- `front-end developer` → `Frontend Developer`
- `fullstack reviewer` → `Fullstack Reviewer`
- `devop` → `DevOps Engineer`
- `product owner` → `Scrum Master / Product Owner`
- `qa/test engineer` → `QA/Test Engineer`

Ausführen (Windows PowerShell):

```powershell
python .\tools\process_todos.py
```

Die Skriptausgabe zeigt pro Aufgabe die angewandten Prompts und schreibt die Ergebnisse in `tasks_history/`.
