# Ananta

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Persistente Daten wie Konfigurationen, Aufgaben, Logs und Steuerflags werden in PostgreSQL gespeichert.

## High-Level Objectives

- Document core architecture and establish coding conventions.
- Deliver a usable dashboard with environment setup guidance.
- Automate testing and deployment workflows.

Weitere Details siehe [Product Roadmap](docs/roadmap.md).

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
- `src/` – Backend-Quellcode und Hilfsmodule

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

### Controller (`controller/controller.py`)

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/next-config` | GET | Nächste Agenten-Konfiguration inkl. Aufgaben & Templates. |
| `/config` | GET | Gesamte Controller-Konfiguration aus der Datenbank. |
| `/config/api_endpoints` | POST | Aktualisiert die LLM-Endpunkte. |
| `/approve` | POST | Validiert und führt Agenten-Vorschläge aus. |
| `/issues` | GET | Holt GitHub-Issues und reiht Aufgaben ein. |
| `/set_theme` | POST | Speichert Dashboard-Theme im Cookie. |
| `/` | GET/POST | HTML-Dashboard für Pipeline- und Agentenverwaltung. |
| `/agent/<name>/toggle_active` | POST | Schaltet `controller_active` eines Agents um. |
| `/agent/<name>/log` | GET/DELETE | Liefert oder löscht Logeinträge eines Agents aus der Datenbank. |
| `/agent/add_task` | POST | Fügt eine Aufgabe zur globalen Liste hinzu. |
| `/agent/<name>/tasks` | GET | Zeigt aktuelle und anstehende Aufgaben eines Agents. |
| `/stop`, `/restart` | POST | Setzt Stop-Flags in der Datenbank. |
| `/export` | GET | Exportiert Logs und Konfigurationen als ZIP. |
| `/ui`, `/ui/<pfad>` | GET | Serviert das gebaute Vue-Frontend. |

### Blueprint-Routen (`src/controller/routes.py`)

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/controller/next-task` | GET | Nächste nicht gesperrte Aufgabe. |
| `/controller/blacklist` | GET/POST | Liest oder ergänzt die Blacklist. |
| `/controller/status` | GET/DELETE | Interner Log-Status des `ControllerAgent` oder Leeren. |

### AI-Agent (`agent/ai_agent.py`)

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/health` | GET | Gesundheitscheck des Agents. |
| `/logs` | GET | Liefert protokollierte Einträge des laufenden Agents. |
| `/tasks` | GET | Aktuelle und ausstehende Tasks für den Agenten. |
| `/stop` | POST | Setzt ein Stop-Flag, das den Polling-Loop beendet. |
| `/restart` | POST | Entfernt das Stop-Flag und erlaubt weiteren Polling-Betrieb. |

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

## Fehlersuche

Falls Fehler beim Starten der Container auftreten, überprüfen Sie:

1. Sind alle benötigten Dateien vorhanden (insbesondere im Pfad `agent/ai_agent.py`)?
2. Stimmen die Pfade im Dockerfile mit der tatsächlichen Projektstruktur überein?
3. Sind die Umgebungsvariablen korrekt gesetzt?

Siehe auch die README-Dateien in den jeweiligen Unterverzeichnissen für mehr Details.

## Weitere Dokumentation

- [src/README.md](src/README.md) – Übersicht über den Backend-Code.
- [frontend/README.md](frontend/README.md) – Nutzung des Vue-Dashboards.
- [docs/dashboard.md](docs/dashboard.md) – Architektur und zentrale API-Endpunkte des Dashboards.
- [docs/roadmap.md](docs/roadmap.md) – Produkt-Roadmap und Ziele.
