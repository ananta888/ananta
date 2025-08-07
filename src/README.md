# Backend-Quellcode

## Agents
- `agents/base.py` – `Agent`-Dataclass und Utilities zum Laden von Agenten aus Konfigurationsdaten.
- `agents/__init__.py` – `load_agents()` registriert mehrere Agenten aus Datenbankkonfigurationen.
- `agents/templates.py` – `PromptTemplates`-Registry zum Verwalten und Rendern von Prompt-Vorlagen.

## Controller
- `controller/agent.py` – `ControllerAgent` erweitert `Agent` um Aufgabenverteilung, Blacklist-Verwaltung und Logging.
- `controller/routes.py` – Zusätzliche HTTP-Routen mit Blueprint unter `/controller`.

## Models
- `models/pool.py` – `ModelPool` mit `register`, `acquire`, `release` und `status` zur Limitierung und Einsicht paralleler LLM-Anfragen.

## Skripte
- `../agent/ai_agent.py` – Hauptschleife des AI-Agents.
- `../controller/controller.py` – Flask-Server und Datenbankgestützte Konfigurationsverwaltung.
