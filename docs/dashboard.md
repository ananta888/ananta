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

```bash
# Agent Health
curl http://localhost:5001/health

# Config lesen/setzen (mit Token)
curl http://localhost:5001/config
curl -X POST http://localhost:5001/config \
  -H "Authorization: Bearer secret1" -H "Content-Type: application/json" \
  -d '{"provider":"ollama","model":"llama3"}'

# Propose/Execute (Worker)
curl -X POST http://localhost:5001/step/propose -H "Content-Type: application/json" \
  -d '{"prompt":"REASON/COMMAND format..."}'
curl -X POST http://localhost:5001/step/execute \
  -H "Authorization: Bearer secret1" -H "Content-Type: application/json" \
  -d '{"command":"echo hello"}'

# Hub: Task anlegen → zuweisen → ausführen → logs
curl -X POST http://localhost:5000/tasks \
  -H "Authorization: Bearer hubsecret" -H "Content-Type: application/json" \
  -d '{"title":"Demo-Task"}'
curl -X POST http://localhost:5000/tasks/T-XXXXXX/assign \
  -H "Authorization: Bearer hubsecret" -H "Content-Type: application/json" \
  -d '{"agent_url":"http://localhost:5001","token":"secret1"}'
curl -X POST http://localhost:5000/tasks/T-XXXXXX/step/propose -H "Content-Type: application/json" -d '{}'
curl -X POST http://localhost:5000/tasks/T-XXXXXX/step/execute \
  -H "Authorization: Bearer hubsecret" -H "Content-Type: application/json" -d '{}'
curl http://localhost:5000/tasks/T-XXXXXX/logs
```

## Entwicklungsbefehle (Angular)

```bash
cd frontend-angular
npm start         # Entwicklungsserver
npm run build     # Produktionsbuild (dist/)
```

Hinweis: Es gibt keinen separaten Controller oder DB‑Server mehr; alle Daten liegen lokal bei den Agenten (JSON/JSONL).
