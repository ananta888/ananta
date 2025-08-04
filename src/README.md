# Backend-Quellcode

## Agents
- `agents/base.py` – `Agent`-Dataclass und `from_file()` zum Einlesen von JSON-Konfigurationen.
- `agents/__init__.py` – `load_agents()` lädt mehrere Agenten-Configs aus einem Verzeichnis.
- `agents/templates.py` – `PromptTemplates`-Registry zum Verwalten und Rendern von Prompt-Vorlagen.

## Controller
- `controller/agent.py` – `ControllerAgent` erweitert `Agent` um Aufgabenverteilung, Blacklist-Verwaltung und Logging.
- `controller/routes.py` – Zusätzliche HTTP-Routen mit Blueprint unter `/controller`.

## Models
- `models/pool.py` – `ModelPool` mit `register`, `acquire` und `release` zur Limitierung paralleler LLM-Anfragen.

## Skripte
- `../agent/ai_agent.py` – Hauptschleife des AI-Agents.
- `../controller/controller.py` – Flask-Server und Konfigurationsverwaltung.

