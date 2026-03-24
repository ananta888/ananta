# AI-Agent (Flask API)

Leichter Python-Agent als API-Server mit Hub/Worker-Rolle.

## Rollen
- `ROLE=hub`: zentrale Orchestrierung fuer Tasks, Teams, Templates
- `ROLE=worker`: Ausfuehrung von LLM-Schritten und Shell-Kommandos

## Wichtige Endpunkte
- `GET /health`
- `GET /config`, `POST /config`
- `POST /step/propose`, `POST /step/execute`
- Hub-spezifisch: `/tasks*`, `/templates*`, `/teams*`
- Blueprint-first Teams: `/teams/blueprints*`, `/teams/setup-scrum`

## Start
```bash
pip install -r requirements.txt
python -m agent.ai_agent
```

## Persistenz
- SQLModel-Datenbank (PostgreSQL/SQLite)
- Laufzeit-Logs unter `data/terminal_log.jsonl`

## Dokumentation
- Backend-Details: `docs/backend.md`
- API-Spec: `api-spec.md`
- Architekturmodell fuer Blueprints/Teams: `architektur/README.md`
