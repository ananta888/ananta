# Coding Conventions

## Backend (Python)

- Bevorzugt Typen fÃ¼r Request/Response-Modelle (Pydantic/SQLModel).
- Repository-Layer fÃ¼r DB-Zugriffe verwenden (`agent/repository.py`).
- Logging mit Correlation-ID nutzen (`agent.common.logging`).

## Frontend (Angular)

- Komponenten mÃ¼ssen standalone sein (siehe bestehende Components).
- Services zentralisieren API-Zugriffe (`hub-api.service.ts`, `agent-api.service.ts`).
- UI-State Ã¼ber Services/Observables; lokale Component-States sparsam.

## Tests

- Backend: `python -m unittest discover tests`
- Frontend: `npm run test:e2e` (Playwright)
