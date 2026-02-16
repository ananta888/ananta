# Architekturplan fuer Ananta

Dieses Dokument beschreibt die Hub-Worker-Architektur und die zentralen Laufzeitpfade.

## Komponenten
- Hub (ROLE=hub): Registry, Task-Orchestrierung, Team- und Template-Verwaltung
- Worker (ROLE=worker): LLM-Integration, Kommandoausfuehrung, Log-Reporting
- Frontend (Angular 21): Dashboard fuer Steuerung und Monitoring

## Datenfluesse
1. Worker registrieren sich am Hub (`POST /register`).
2. Tasks werden ueber den Hub angelegt und zugewiesen.
3. Propose/Execute laeuft lokal oder via Forwarding an zugewiesene Worker.
4. Logs werden taskbezogen gesammelt und angezeigt.

## Technologie-Stack
- Backend: Python 3.11+, Flask, SQLModel
- Frontend: Angular 21
- Persistenz: PostgreSQL/SQLite
- Queue/Cache: Redis (Compose Standard)

## Referenzen
- Backend-Doku: `docs/backend.md`
- UML-Diagramme: `architektur/uml/`