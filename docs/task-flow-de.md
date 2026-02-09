# Aufgabenfluss (Task-Logik) von Angular zum Hub/Worker-Agent

Dieser Leitfaden beschreibt die aktuelle End-to-End-Logik fuer Aufgaben (Tasks) im Ananta-System: vom Angular-Frontend ueber den Hub-Agent bis zum Worker-Agent. Er spiegelt die aktuelle Architektur (Hub/Worker, SQLModel/DB) wider.

## Ueberblick

- **Frontend (Angular SPA):** Erstellt und verwaltet Tasks sowie Templates und Teams.
- **Hub-Agent (ROLE=hub):** Persistiert Tasks, Templates, Teams und Rollen in der DB, leitet Propose/Execute an Worker weiter.
- **Worker-Agent (ROLE=worker):** Fuehrt Shell-Kommandos aus und liefert Logs zurueck.

## Frontend: Tasks erstellen und verwalten

Relevante Komponenten:

- `frontend-angular/src/app/components/board.component.ts` (Board-Ansicht)
- `frontend-angular/src/app/components/task-detail.component.ts` (Detail, Propose/Execute/Logs)
- `frontend-angular/src/app/services/hub-api.service.ts` (HTTP-Client)

API-Calls des Frontends:

- `POST /tasks` - Task anlegen (Titel, Status, Template, Tags, usw.)
- `PATCH /tasks/{id}` - Status, Zuweisung und Felder aktualisieren
- `POST /tasks/{id}/assign` - Worker-Agent zuweisen
- `POST /tasks/{id}/step/propose` - LLM-Vorschlag erzeugen
- `POST /tasks/{id}/step/execute` - Kommando ausfuehren
- `GET /tasks/{id}/logs` - Logs fuer den Task

## Hub-Agent: Persistenz und Orchestrierung

Dateien/Module:

- `agent/routes/tasks.py` - Endpunkte fuer Task-CRUD, Propose/Execute, Logs
- `agent/db_models.py` - SQLModel-Tabellen (TaskDB, TeamDB, TemplateDB, etc.)
- `agent/repository.py` - Datenzugriff (Repository-Layer)

Der Hub speichert Tasks in der Datenbank (Postgres/SQLite via SQLModel). Bei Propose/Execute entscheidet der Hub:

1. **Kein Worker zugewiesen:** Propose/Execute lokal auf dem Hub.
2. **Worker zugewiesen:** Request wird an den Worker weitergeleitet (inkl. Token).

Logs werden im Hub im Task-Kontext aggregiert und ueber `/tasks/{id}/logs` bereitgestellt.

## Worker-Agent: Ausfuehrung und Logs

Der Worker empfaengt Propose/Execute ueber:

- `POST /step/propose`
- `POST /step/execute`

Ausfuehrungen werden geloggt und an den Hub weitergereicht. Die Logs landen im zentralen Task-Kontext.

## Sequenz (Kurzfassung)

1. User erstellt Task im Angular-UI.
2. Hub speichert Task in der DB.
3. Task wird einem Worker zugewiesen.
4. Propose/Execute wird an den Worker delegiert.
5. Worker liefert Output/Logs zurueck; Hub stellt Logs bereit.

## Wichtige Umgebungsvariablen

- `ROLE` - `hub` oder `worker`
- `AGENT_TOKEN` - Admin/Agent-Token fuer schreibende Endpunkte
- `DATABASE_URL` - DB-Verbindung (Postgres oder SQLite)

## Tests

- `tests/test_task_*` - Backend-Task-Endpoints
- `frontend-angular/tests/*` - Playwright E2E-Tests (Dashboard/Board/Panel)
