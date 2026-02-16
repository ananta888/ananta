# Ananta

Modulares Multi-Agent-System fuer AI-gestuetzte Entwicklung mit Hub-Worker-Architektur.

## Architektur
- Angular Frontend fuer Visualisierung und Steuerung
- Hub-Agent fuer Orchestrierung (Tasks, Teams, Templates)
- Worker-Agenten fuer LLM-gestuetzte Ausfuehrung
- Persistenz via PostgreSQL (Standard) oder SQLite

Details: `docs/backend.md` und `architektur/README.md`.

## Quickstart (Docker)
1. Vorbereitung:
```bash
cp .env.example .env
```
2. Start:
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d
```
3. Zugriff:
- Frontend: `http://localhost:4200`
- Hub API: `http://localhost:5000`

## Entwicklung und Qualitaet
- Backend lokal: `agent/README.md`
- Frontend lokal: `frontend-angular/README.md`
- Backend-Tests: `pytest`
- Frontend E2E: `cd frontend-angular && npm run test:e2e`

Linting:
- Backend: `python -m flake8 agent tests`
- Security-Lint (zusaetzlich in separatem CI-Job): `ruff check agent/ --select=E,F,W,S603,S607`
- Frontend: `cd frontend-angular && npm run lint`

## Weiterfuehrende Dokumentation
- `docs/INSTALL_TEST_BETRIEB.md`
- `api-spec.md`
- `docs/backend.md`
- `docs/roadmap.md`
- `docs/coding-conventions.md`