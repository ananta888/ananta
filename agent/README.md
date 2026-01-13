# AI-Agent (Flask API)

Leichter Python‑Agent, der ein Terminal über LLM‑generierte Shell‑Befehle steuert. Der Agent ist ein eigenständiger Flask‑API‑Server und nutzt Postgres (oder SQLite als Fallback) für die Datenspeicherung.

## Endpunkte

- GET `/health` → `{ "status": "ok" }`
- GET `/config` / POST `/config` → Agent‑Konfiguration (persistiert in `data/config.json`)
- POST `/step/propose` → LLM schlägt einen Befehl vor (noch ohne Ausführung)
- POST `/step/execute` → führt einen Befehl aus (optional mit `task_id`)
- GET `/logs?limit=&task_id=` → letzte Einträge aus `data/terminal_log.jsonl`

Optionaler Hub‑Modus (`ROLE=hub`):
- Templates: GET/POST/PATCH/DELETE `/templates*`
- Tasks: GET/POST/GET/PATCH `/tasks*`, plus `/tasks/{id}/assign|step/propose|step/execute|logs`

## Start

```bash
pip install -r requirements.txt
python -m agent.ai_agent
# lauscht auf 0.0.0.0:${PORT:-5000}
```

## Konfiguration über ENV

- `AGENT_NAME` – Anzeigename (Default: "default")
- `AGENT_TOKEN` – optionaler Bearer‑Token für schreibende Endpunkte
- `ROLE` – `worker` (Default) oder `hub`
- `PORT` – Port der Flask‑App (Default: 5000)
- `OLLAMA_URL` – Default `http://localhost:11434/api/generate`
- `LMSTUDIO_URL` – Default `http://localhost:1234/v1/completions`
- `OPENAI_URL` – Default `https://api.openai.com/v1/chat/completions`
- `OPENAI_API_KEY` – API‑Key für OpenAI (falls genutzt)

## Logs & Persistenz

- Ausführungen werden zeilenweise in `data/terminal_log.jsonl` abgelegt.
- Konfiguration wird in `data/config.json` gespeichert.

## Hub‑Modus (ROLE=hub)

Wenn die Umgebungsvariable `ROLE=hub` gesetzt ist, erweitert der Agent seine API um eine Aufgaben‑ und Template‑Orchestrierung. Der Hub speichert seine Daten primär in einer Postgres-Datenbank (siehe `DATABASE_URL`) und kann Ausführungen an Worker‑Agenten weiterleiten.

Zweck
- Zentrale Verwaltung von Tasks und Templates für ein Team (Scrum‑tauglich: Backlog/To‑Do/In‑Progress/Done)
- Orchestrierung: Propose/Execute wird je nach Assignment an einen Worker‑Agenten „weitergereicht“
- Aggregation von Logs pro Task (lesen aus `data/terminal_log.jsonl`)

Ablage (Standard‑Pfade)
- `data/templates.json` – Liste der Templates
- `data/tasks.json` – Map `task_id -> Task`
- `data/terminal_log.jsonl` – JSON Lines, u. a. mit `task_id`

Zusätzliche Endpunkte des Hubs
- Templates
  - `GET /templates` – alle Templates
  - `POST /templates` – Template anlegen (Body: `{ name, description, prompt_template, provider?, model?, defaults? }`)
  - `PATCH /templates/{id}` – Template aktualisieren
  - `DELETE /templates/{id}` – Template löschen
- Tasks
  - `GET /tasks` – alle Tasks (einfache Liste)
  - `POST /tasks` – Task anlegen (Body: `{ title, description?, template_id?, tags?, status? }`)
  - `GET /tasks/{id}` – Task lesen
  - `PATCH /tasks/{id}` – Felder aktualisieren (z. B. `status`)
  - `POST /tasks/{id}/assign` – Zuweisung setzen `{ agent_url, token? }`
  - `POST /tasks/{id}/step/propose` – Propose per Worker (oder lokal)
  - `POST /tasks/{id}/step/execute` – Execute per Worker (oder lokal), loggt mit `task_id`
  - `GET /tasks/{id}/logs` – gefilterte Logs (aus `terminal_log.jsonl`)

Sicherheit
- Schreibende Endpunkte des Hubs (POST/PUT/PATCH/DELETE) erfordern den Hub‑Token (`AGENT_TOKEN`).
- Bei Forwarding an Worker berücksichtigt der Hub den in der Assignment hinterlegten Token und sendet ihn als `Authorization: Bearer <token>` an den Worker.

Beispiel‑Flow (per curl)
```
# 1) Hub starten (Port 5000), Worker auf 5001/5002
#    ENV (z. B. docker-compose): ROLE=hub, AGENT_TOKEN=hubsecret

# 2) Task anlegen
curl -X POST http://localhost:5000/tasks \
  -H "Authorization: Bearer hubsecret" \
  -H "Content-Type: application/json" \
  -d '{"title":"Repo-Analyse"}'

# 3) Zuweisen an Worker
curl -X POST http://localhost:5000/tasks/T-123456/assign \
  -H "Authorization: Bearer hubsecret" \
  -H "Content-Type: application/json" \
  -d '{"agent_url":"http://localhost:5001","token":"secret1"}'

# 4) Vorschlag holen
curl -X POST http://localhost:5000/tasks/T-123456/step/propose \
  -H "Content-Type: application/json" \
  -d '{"prompt":"REASON/COMMAND format..."}'

# 5) Ausführen
curl -X POST http://localhost:5000/tasks/T-123456/step/execute \
  -H "Authorization: Bearer hubsecret" \
  -H "Content-Type: application/json" \
  -d '{"command":"echo hello"}'

# 6) Logs zum Task
curl http://localhost:5000/tasks/T-123456/logs
```

Docker‑Compose‑Beispiel (Auszug)
```
services:
  ai-agent-hub:
    image: python:3.11-slim
    environment:
      - ROLE=hub
      - AGENT_NAME=hub
      - AGENT_TOKEN=hubsecret
    volumes:
      - ./agent:/app/agent
      - ./data/hub:/app/data
    command: sh -lc "pip install -r requirements.txt && python -m agent.ai_agent"
    ports:
      - "5000:5000"
```

Grenzen & Hinweise
- Der Hub nutzt eine Postgres-Datenbank für Persistenz (konfigurierbar via `DATABASE_URL`).
- Logs werden aktuell gepollt; ein SSE‑Endpoint (`/events`) ist optional und kann später ergänzt werden.
- Das Angular‑Frontend erwartet CORS‑freigeschaltete Agenten und nutzt je Agent den passenden Token.

## Hinweise

- CORS ist aktiviert, damit das Angular‑Frontend direkt gegen den Agenten sprechen kann.
- Schreibende Endpunkte verlangen bei gesetztem `AGENT_TOKEN` einen `Authorization: Bearer <token>` Header.

## Troubleshooting LLM-Verbindung

Falls Fehlermeldungen wie `Connection refused` bei `host.docker.internal` auftreten:
- **Ollama**: Muss auf `0.0.0.0` lauschen. Setzen Sie `OLLAMA_HOST=0.0.0.0` bevor Sie Ollama starten.
- **LMStudio**: Stellen Sie in den Server-Einstellungen den Host auf `0.0.0.0` (all interfaces) um. In Version 0.3.x findet sich dies unter dem Icon `<->` (Local Server) -> Network Settings -> Schalter "Im lokalen Netzwerk bereitstellen".
- **Wichtig**: Falls LMStudio die falsche IP wählt oder auf `127.0.0.1` bleibt, nutzen Sie den `netsh` Portproxy-Workaround (siehe `docs/INSTALL_TEST_BETRIEB.md`).
- Siehe detaillierte Anleitung in `docs/INSTALL_TEST_BETRIEB.md`.
