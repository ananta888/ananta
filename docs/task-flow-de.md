# Aufgabenfluss (Task-Logik) von Angular zum Hub/Worker-Agent

Dieser Leitfaden beschreibt die aktuelle End-to-End-Logik fÃ¼r Aufgaben (Tasks) im Ananta-System: vom Angular-Frontend Ã¼ber den Hub-Agent bis zum Worker-Agent. Er spiegelt die aktuelle Architektur (Hub/Worker, SQLModel/DB) wider.

## Ãœberblick

- **Frontend (Angular SPA):** Erstellt und verwaltet Tasks sowie Templates und Teams.
- **Hub-Agent (ROLE=hub):** Persistiert Tasks, Templates, Teams und Rollen in der DB, leitet Propose/Execute an Worker weiter.
- **Worker-Agent (ROLE=worker):** FÃ¼hrt Shell-Kommandos aus und liefert Logs zurÃ¼ck.

## Frontend: Tasks erstellen und verwalten

Relevante Komponenten:

- `frontend-angular/src/app/components/board.component.ts` (Board-Ansicht)
- `frontend-angular/src/app/components/task-detail.component.ts` (Detail, Propose/Execute/Logs)
- `frontend-angular/src/app/services/hub-api.service.ts` (HTTP-Client)

API-Calls des Frontends:

- `POST /tasks` â€“ Task anlegen (Titel, Status, Template, Tags, usw.)
- `PATCH /tasks/{id}` â€“ Status, Zuweisung und Felder aktualisieren
- `POST /tasks/{id}/assign` â€“ Worker-Agent zuweisen
- `POST /tasks/{id}/step/propose` â€“ LLM-Vorschlag erzeugen
- `POST /tasks/{id}/step/execute` â€“ Kommando ausfÃ¼hren
- `GET /tasks/{id}/logs` â€“ Logs fÃ¼r den Task

## Hub-Agent: Persistenz und Orchestrierung

Dateien/Module:

- `agent/routes/tasks.py` â€“ Endpunkte fÃ¼r Task-CRUD, Propose/Execute, Logs
- `agent/db_models.py` â€“ SQLModel-Tabellen (TaskDB, TeamDB, TemplateDB, etc.)
- `agent/repository.py` â€“ Datenzugriff (Repository-Layer)

Der Hub speichert Tasks in der Datenbank (Postgres/SQLite via SQLModel). Bei Propose/Execute entscheidet der Hub:

1. **Kein Worker zugewiesen:** Propose/Execute lokal auf dem Hub.
2. **Worker zugewiesen:** Request wird an den Worker weitergeleitet (inkl. Token).

Logs werden im Hub im Task-Kontext aggregiert und Ã¼ber `/tasks/{id}/logs` bereitgestellt.

## Worker-Agent: AusfÃ¼hrung und Logs

Der Worker empfÃ¤ngt Propose/Execute Ã¼ber:

- `POST /step/propose`
- `POST /step/execute`

AusfÃ¼hrungen werden geloggt und an den Hub weitergereicht. Die Logs landen im zentralen Task-Kontext.

## Sequenz (Kurzfassung)

1. User erstellt Task im Angular-UI.
2. Hub speichert Task in der DB.
3. Task wird einem Worker zugewiesen.
4. Propose/Execute wird an den Worker delegiert.
5. Worker liefert Output/Logs zurÃ¼ck; Hub stellt Logs bereit.

## Wichtige Umgebungsvariablen

- `ROLE` â€“ `hub` oder `worker`
- `AGENT_TOKEN` â€“ Admin/Agent-Token fÃ¼r schreibende Endpunkte
- `DATABASE_URL` â€“ DB-Verbindung (Postgres oder SQLite)

## Tests

- `tests/test_task_*` â€“ Backend-Task-Endpoints
- `frontend-angular/tests/*` â€“ Playwright E2E-Tests (Dashboard/Board/Panel)
