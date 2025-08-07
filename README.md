# Ananta

Ananta ist ein modulares Multi-Agenten-System mit einem Flask-basierten Controller, einem Python-Agenten und einem Vue-Dashboard. Persistente Daten wie Konfigurationen, Aufgaben, Logs und Steuerflags werden vollständig in einer PostgreSQL-Datenbank gespeichert.

## Komponenten

### Controller (Flask-Server)
- Verwaltet Konfiguration, Aufgabenliste, Blacklist und Log-Export über PostgreSQL.
- Stellt HTTP-Endpunkte für Agenten, Dashboard und das gebaute Vue-Frontend bereit.

### AI-Agent (`agent/ai_agent.py`)
- Pollt den Controller, rendert Prompts aus Templates und führt bestätigte Kommandos aus.
- Unterstützt mehrere LLM-Provider (Ollama, LM Studio, OpenAI) über konfigurierbare Endpunkte.
- Nutzt `ModelPool`, um gleichzeitige Modellanfragen pro Provider/Modell zu begrenzen.
- Schreibt eigene Einstellungen und Laufzeit-Logs in die Schema `agent` der Datenbank.

### Frontend (`frontend/`)
- Vue-Dashboard zur Anzeige von Logs und Steuerung der Agenten.
- Kommuniziert über Fetch-Aufrufe mit dem Controller.

## Wichtige Module

| Pfad | Zweck |
| ---- | ----- |
| `src/agents/` | Agent-Dataclass und Prompt-Template-Utilities. |
| `src/controller/` | `ControllerAgent` und zusätzliche HTTP-Routen. |
| `src/models/pool.py` | `ModelPool` zur Limitierung paralleler LLM-Anfragen. |
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
| `/agent/<name>/log` | GET | Liefert Logeinträge eines Agents aus der Datenbank. |
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
| `/controller/status` | GET | Interner Log-Status des `ControllerAgent`. |

### AI-Agent (`agent/ai_agent.py`)

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/agent/config` | GET/POST | Liest oder schreibt die Agent-Konfiguration aus/in PostgreSQL. |
| `/agent/<name>/log` | GET | Gibt Logs eines Agents zurück. |

## Ablauf

1. **Startup** – Beim ersten Start wird die Datenbank initialisiert und mit den Daten aus `config.json` befüllt.
2. **Agentenlauf** – `agent/ai_agent.py` pollt `/next-config`, erstellt Prompts und ruft LLMs auf; Ergebnisse werden über `/approve` bestätigt und ausgeführt.
3. **Dashboard** – Vue-UI und HTML-Views nutzen Controller-Endpunkte, um Statusinformationen aus der Datenbank anzuzeigen und Eingriffe zu ermöglichen.

## Persistenz der Konfiguration

- Alle Konfigurations- und Logdaten liegen in PostgreSQL. Die Datei `config.json` dient nur als Vorlage für die initiale Befüllung.
- Die Verbindung wird über die Umgebungsvariable `DATABASE_URL` konfiguriert. Docker Compose startet automatisch einen PostgreSQL-Container und setzt diese Variable für Controller und Agent.

## Erweiterbarkeit

- Zusätzliche Agenten über SQL-Inserts in die Config verwalten.
- Neue Prompt-Templates in der Datenbank registrieren.
- `ModelPool` erlaubt konfigurationsabhängiges Throttling pro Provider/Modell.

## Weitere Dokumentation

- [src/README.md](src/README.md) – Übersicht über den Backend-Code.
- [frontend/README.md](frontend/README.md) – Nutzung des Vue-Dashboards.
- [docs/dashboard.md](docs/dashboard.md) – Architektur und zentrale API-Endpunkte des Dashboards.
