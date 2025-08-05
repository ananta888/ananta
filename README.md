# Ananta

Ananta ist ein modulares Multi-Agenten-System mit einem Flask-basierten Controller, einem Python-Agenten und einem Vue-Dashboard.

## Komponenten

### Controller (Flask-Server)
- Verwaltet Agenten-Konfiguration (`config.json`), Aufgabenliste, Blacklist und Log-Export.
- Stellt HTTP-Endpunkte für Agenten, Dashboard und ein gebautes Vue-Frontend bereit.

### AI-Agent (`agent/ai_agent.py`)
- Pollt den Controller, rendert Prompts aus Templates und führt bestätigte Kommandos aus.
- Unterstützt mehrere LLM-Provider (Ollama, LM Studio, OpenAI) über konfigurierbare Endpunkte.
- Nutzt `ModelPool`, um gleichzeitige Modellanfragen pro Provider/Modell zu begrenzen.

### Frontend (`frontend/`)
- Vue-Dashboard zur Anzeige von Logs und Steuerung der Agenten.
- Kommuniziert über Fetch-Aufrufe mit dem Controller.

## Wichtige Module

| Pfad | Zweck |
| ---- | ----- |
| `src/agents/` | Agent-Dataclass, `load_agents()`-Helfer und Prompt-Template-Utilities. |
| `src/controller/` | `ControllerAgent` und zusätzliche HTTP-Routen. |
| `src/models/pool.py` | `ModelPool` zur Limitierung paralleler LLM-Anfragen. |
| `agent/ai_agent.py` | Hilfsfunktionen und Hauptschleife des Agents. |
| `controller/controller.py` | Konfigurationsverwaltung und HTTP-Endpoint-Definitionen. |

## HTTP-Endpunkte

### Controller (`controller/controller.py`)

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/next-config` | GET | Nächste Agenten-Konfiguration inkl. Aufgaben & Templates. |
| `/config` | GET | Gesamte Controller-Konfiguration als JSON. |
| `/config/api_endpoints` | POST | Aktualisiert die LLM-Endpunkte in `config.json`. |
| `/approve` | POST | Validiert und führt Agenten-Vorschläge aus. |
| `/issues` | GET | Holt GitHub-Issues und reiht Aufgaben ein. |
| `/set_theme` | POST | Speichert Dashboard-Theme im Cookie. |
| `/` | GET/POST | HTML-Dashboard für Pipeline- und Agentenverwaltung. |
| `/agent/<name>/toggle_active` | POST | Schaltet `controller_active` eines Agents um. |
| `/agent/<name>/log` | GET | Liefert Logdatei eines Agents. |
| `/stop`, `/restart` | POST | Legt `stop.flag` an bzw. entfernt ihn. |
| `/export` | GET | Exportiert Logs und Konfigurationen als ZIP. |
| `/ui`, `/ui/<pfad>` | GET | Serviert das gebaute Vue-Frontend. |

### Blueprint-Routen (`src/controller/routes.py`)

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/controller/next-task` | GET | Nächste nicht gesperrte Aufgabe. |
| `/controller/blacklist` | GET/POST | Liest oder ergänzt die Blacklist. |
| `/controller/status` | GET | Interner Log-Status des `ControllerAgent`. |

## Ablauf

1. **Startup** – Controller initialisiert `config.json` und optional `default_team_config.json`; `ModelPool` registriert Limits.
2. **Agentenlauf** – `agent/ai_agent.py` pollt `/next-config`, erstellt Prompts und ruft LLMs auf; Ergebnisse werden über `/approve` bestätigt und ausgeführt.
3. **Dashboard** – Vue-UI und HTML-Views nutzen Endpunkte wie `/config` oder `/agent/<name>/log`, um Status anzuzeigen und Eingriffe zu ermöglichen.

## Erweiterbarkeit

- Zusätzliche Agenten-JSON-Dateien über `load_agents()` einbinden.
- Neue Prompt-Templates mit `PromptTemplates` hinzufügen.
- `ModelPool` erlaubt konfigurationsabhängiges Throttling pro Provider/Modell.

## Weitere Dokumentation

- [src/README.md](src/README.md) – Übersicht über den Backend-Code.
- [frontend/README.md](frontend/README.md) – Nutzung des Vue-Dashboards.
- [docs/dashboard.md](docs/dashboard.md) – Architektur und zentrale API-Endpunkte des Dashboards.

