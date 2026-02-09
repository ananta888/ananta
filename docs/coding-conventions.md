# Coding Conventions

## Backend (Python)

- Bevorzugt Typen fuer Request/Response-Modelle (Pydantic/SQLModel).
- Repository-Layer fuer DB-Zugriffe verwenden (`agent/repository.py`).
- Logging mit Correlation-ID nutzen (`agent.common.logging`).

## Frontend (Angular)

- Komponenten muessen standalone sein (siehe bestehende Components).
- Services zentralisieren API-Zugriffe (`hub-api.service.ts`, `agent-api.service.ts`).
- UI-State ueber Services/Observables; lokale Component-States sparsam.

## Tests

- Backend: `python -m unittest discover tests`
- Frontend: `npm run test:e2e` (Playwright)
