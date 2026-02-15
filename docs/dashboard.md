# Dashboard-Architektur und API-Übersicht (aktualisiert)

Diese Seite beschreibt die neue, vereinfachte Architektur des Dashboards und listet die relevanten HTTP‑Endpunkte auf. Das System besteht jetzt aus zwei Komponenten:

- Angular‑Frontend (SPA)
- Mehrere `ai_agent.py`‑Instanzen (Flask‑APIs), optional mit Hub‑Rolle (`ROLE=hub`)

## Architektur

1. Die Angular‑SPA spricht direkt per HTTP (CORS) mit beliebigen Agent‑Instanzen.
2. Optional übernimmt eine Agent‑Instanz im Hub‑Modus (`ROLE=hub`) die Verwaltung von Tasks/Templates und leitet Ausführungen an Worker‑Agenten weiter.
3. Jeder Agent loggt Ausführungen lokal in `data/terminal_log.jsonl`; der Hub stellt Task‑bezogene Logansichten bereit.

## Wichtige API‑Endpunkte

Agent (gemeinsame Basis‑API, für Hub und Worker):

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/health` | GET | Healthcheck `{ status: "ok" }` |
| `/config` | GET | Aktuelle Agent‑Konfiguration |
| `/config` | POST | Agent‑Konfiguration setzen (optional Token nötig) |
| `/step/propose` | POST | LLM‑Vorschlag (REASON/COMMAND‑Format), keine Ausführung |
| `/step/execute` | POST | Kommando ausführen (optional `task_id`) |
| `/logs?limit=&task_id=` | GET | Letzte Logs, optional nach `task_id` gefiltert |

Zusätzlich im Hub‑Modus (`ROLE=hub`):

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/templates` | GET/POST/PUT/DELETE | CRUD für Templates |
| `/tasks` | GET/POST | Tasks auflisten/anlegen |
| `/tasks/{id}` | GET/PATCH | Task lesen/aktualisieren (z. B. `status`) |
| `/tasks/{id}/assign` | POST | Task einem Worker zuweisen `{ agent_url, token? }` |
| `/tasks/{id}/step/propose` | POST | Propose per Worker (oder lokal) |
| `/tasks/{id}/step/execute` | POST | Execute per Worker (oder lokal) |
| `/tasks/{id}/logs` | GET | Logs des Tasks |

## Setup (Angular Frontend)

> Erfordert Node.js 18+ und npm.

```bash
cd frontend-angular
npm install
npm start  # http://localhost:4200
```

Oder via Docker Compose:
```bash
docker-compose up -d
# Frontend: http://localhost:4200
# Hub:      http://localhost:5000
# Worker:   http://localhost:5001, http://localhost:5002
```

## API‑Beispiele

### Health-Check (Worker & Hub)
```bash
curl http://localhost:5001/health
```
Response (200 OK):
```json
{
  "status": "ok",
  "version": "0.1.0",
  "role": "worker"
}
```

### Config lesen/setzen (mit Token)
```bash
curl http://localhost:5001/config
```
Response:
```json
{
  "provider": "openai",
  "model": "gpt-4",
  "port": 5001
}
```

```bash
curl -X POST http://localhost:5001/config \
  -H "Authorization: Bearer <worker-token>" -H "Content-Type: application/json" \
  -d '{"provider":"ollama","model":"llama3"}'
```

### Propose/Execute (Worker)
```bash
curl -X POST http://localhost:5001/step/propose -H "Content-Type: application/json" \
  -d '{"prompt":"Hole das aktuelle Wetter"}'
```
Response:
```json
{
  "reasoning": "Ich werde curl nutzen, um eine Wetter-API aufzurufen.",
  "command": "curl -s wttr.in/Berlin?format=3"
}
```

### Hub: Task-Management
**Task anlegen:**
Request:
```bash
curl -X POST http://localhost:5000/tasks \
  -H "Authorization: Bearer <hub-token>" -H "Content-Type: application/json" \
  -d '{"title":"Demo-Task"}'
```
Response (201 Created):
```json
{
  "id": "T-123456",
  "title": "Demo-Task",
  "status": "open",
  "created_at": "2026-02-09T14:35:00Z"
}
```

**Task zuweisen:**
```bash
curl -X POST http://localhost:5000/tasks/T-XXXXXX/assign \
  -H "Authorization: Bearer <hub-token>" -H "Content-Type: application/json" \
  -d '{"agent_url":"http://localhost:5001","token":"<worker-token>"}'

curl -X POST http://localhost:5000/tasks/T-XXXXXX/step/propose \
  -H "Content-Type: application/json" -d '{}'

curl -X POST http://localhost:5000/tasks/T-XXXXXX/step/execute \
  -H "Authorization: Bearer <hub-token>" -H "Content-Type: application/json" \
  -d '{}'

curl http://localhost:5000/tasks/T-XXXXXX/logs
```

## Entwicklungsbefehle (Angular)

```bash
cd frontend-angular
npm start         # Entwicklungsserver
npm run build     # Produktionsbuild (dist/)
```

Hinweis: Persistenz erfolgt in der SQLModel-Datenbank (Postgres/SQLite); Logs liegen weiterhin als JSONL im `data/`-Verzeichnis.


## State-Management & Frontend-Architektur

Das Frontend nutzt primär **RxJS-basierte Services** für das State-Management:
- **AgentApiService / HubApiService**: Kapseln die HTTP-Kommunikation mit den Backends.
- **AgentDirectoryService**: Verwaltet die Liste der bekannten Agent-Instanzen und deren Status.
- **UserAuthService**: Handhabt JWT-basierte Benutzeranmeldung und Rollen.
- **AuthInterceptor**: Fügt automatisch Bearer-Tokens zu ausgehenden Requests hinzu.

Komponenten reagieren auf Datenänderungen mittels `Observable`-Streams (`async pipe`), was eine reaktive UI-Aktualisierung ermöglicht.

## Barrierefreiheit (A11y)

Bei der Entwicklung des Dashboards wird auf Barrierefreiheit geachtet:
- **Semantisches HTML**: Einsatz von `<nav>`, `<main>`, `<section>`, `<header>`, etc.
- **ARIA-Attribute**: Ergänzung von Labels für Icon-Buttons und Status-Indikatoren.
- **Keyboard-Nav**: Alle interaktiven Elemente sind per Tab erreichbar.
- **Automatisierte Checks**: Integration von `axe-core` in Playwright-Tests (`frontend-angular/tests/a11y.spec.ts`).

### Lighthouse-Audit (Schrittfolge)

1. Frontend lokal starten:
```bash
cd frontend-angular
npm start
```
2. In einem zweiten Terminal Lighthouse ausfuehren:
```bash
npx lighthouse http://localhost:4200 \
  --only-categories=accessibility,best-practices \
  --preset=desktop \
  --output=html \
  --output-path=./test-results/lighthouse-dashboard.html
```
3. Ergebnis pruefen:
- Accessibility Score sollte >= 90 sein.
- Kritische Findings mit "serious" oder "critical" im nächsten Sprint beheben.
- Bericht als Build-Artefakt ablegen (`frontend-angular/test-results/`).

## Logs (SSE vs. Polling)

Das Dashboard kann Task-Logs per Polling abrufen oder via SSE (`/tasks/{id}/stream-logs`). SSE ist optional; falls SSE nicht verfügbar ist, nutzt das UI Polling.
