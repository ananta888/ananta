# AI-Agent (Flask API)

Leichter Python-Agent, der ein Terminal über LLM-generierte Shell-Befehle steuert. Der Agent ist ein eigenständiger Flask-API-Server und nutzt Postgres (oder SQLite als Fallback) für die Datenspeicherung.

## Endpunkte

- GET `/health` → `{ "status": "ok" }`

- GET `/config` / POST `/config` → Agent-Konfiguration (persistiert in `data/config.json`)

- POST `/step/propose` → LLM schlägt einen Befehl vor (noch ohne Ausführung)

- POST `/step/execute` → führt einen Befehl aus (optional mit `task_id`)

- GET `/logs?limit=&task_id=` → letzte Einträge aus `data/terminal_log.jsonl`

- POST `/api/sgpt/execute` → Proxy-Endpunkt für [Shell-GPT (SGPT)](https://github.com/ther1d/shell_gpt), ermöglicht die direkte Ausführung von KI-generierten Shell-Befehlen.

- POST `/api/system/csp-report` → Empfängt Content Security Policy (CSP) Verletzungsberichte, loggt diese und speichert sie in den Audit-Logs. (Rate-Limit: 10 Anfragen/Minute)

Optionaler Hub-Modus (`ROLE=hub`):

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

- `AGENT_TOKEN` – optionaler Bearer-Token für schreibende Endpunkte

- `ROLE` – `worker` (Default) oder `hub`

- `PORT` – Port der Flask-App (Default: 5000)

- `OLLAMA_URL` – Default `http://localhost:11434/api/generate`

- `LMSTUDIO_URL` – Default `http://host.docker.internal:1234/v1`

- `OPENAI_URL` – Default `https://api.openai.com/v1/chat/completions`

- `OPENAI_API_KEY` – API-Key für OpenAI (falls genutzt)

- `AGENT_EXTENSIONS` – Komma-separierte Module für Extensions (Blueprint oder `init_app`)

## Logs & Persistenz

- Ausführungen werden zeilenweise in `data/terminal_log.jsonl` abgelegt.

- Konfiguration wird in `data/config.json` gespeichert.

- Tasks, Templates, Teams und Rollen liegen in der SQLModel-Datenbank (Postgres/SQLite).

- Detaillierte Informationen zur Datenbankstruktur finden Sie in [DATABASE.md](./DATABASE.md).

## Hub-Modus (ROLE=hub)

Wenn die Umgebungsvariable `ROLE=hub` gesetzt ist, erweitert der Agent seine API um eine Aufgaben- und Template-Orchestrierung. Der Hub speichert seine Daten primär in einer SQLModel-Datenbank (Postgres/SQLite via `DATABASE_URL`) und kann Ausführungen an Worker-Agenten weiterleiten.

Zweck

- Zentrale Verwaltung von Tasks und Templates für ein Team (Scrum-tauglich: Backlog/To-Do/In-Progress/Done)

- Orchestrierung: Propose/Execute wird je nach Assignment an einen Worker-Agenten „weitergereicht“

- Aggregation von Logs pro Task (lesen aus `data/terminal_log.jsonl`)

Ablage (Standard-Pfade)

- SQLModel-Datenbank (Postgres/SQLite) für Tasks, Templates, Teams, Rollen

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

- Schreibende Endpunkte des Hubs (POST/PUT/PATCH/DELETE) erfordern den Hub-Token (`AGENT_TOKEN`).

- Bei Forwarding an Worker berücksichtigt der Hub den in der Assignment hinterlegten Token und sendet ihn als `Authorization: Bearer <token>` an den Worker.

- LLM-Tool-Calls (`/llm/generate`) sind standardmäßig auf read-only Tools begrenzt; steuerbar über `AGENT_CONFIG.llm_tool_allowlist`/`llm_tool_denylist`.

Beispiel-Flow (per curl)

```

# 1) Hub starten (Port 5000), Worker auf 5001/5002

#    ENV (z. B. .env): ROLE=hub, AGENT_TOKEN_HUB=generate_a_random_token_for_hub

# 2) Task anlegen

curl -X POST http://localhost:5000/tasks \
  -H "Authorization: Bearer generate_a_random_token_for_hub" \
  -H "Content-Type: application/json" \
  -d '{"title":"Repo-Analyse"}'

# 3) Zuweisen an Worker
curl -X POST http://localhost:5000/tasks/T-123456/assign \
  -H "Authorization: Bearer generate_a_random_token_for_hub" \
  -H "Content-Type: application/json" \
  -d '{"agent_url":"http://localhost:5001","token":"generate_a_random_token_for_alpha"}'

# 4) Vorschlag holen

curl -X POST http://localhost:5000/tasks/T-123456/step/propose \

  -H "Content-Type: application/json" \

  -d '{"prompt":"REASON/COMMAND format..."}'

# 5) Ausführen
curl -X POST http://localhost:5000/tasks/T-123456/step/execute \
  -H "Authorization: Bearer generate_a_random_token_for_hub" \
  -H "Content-Type: application/json" \
  -d '{"command":"echo hello"}'

# 6) Logs zum Task

curl http://localhost:5000/tasks/T-123456/logs

```

Docker-Compose-Beispiel (Auszug)

```

services:

  ai-agent-hub:

    image: python:3.11-slim

    environment:

      - ROLE=hub

      - AGENT_NAME=hub

      - AGENT_TOKEN=hub_token_change_me

    volumes:

      - ./agent:/app/agent

      - ./data/hub:/app/data

    command: sh -c "pip install -r requirements.txt && python -m agent.ai_agent"

    ports:

      - "5000:5000"

```

Grenzen & Hinweise

- Der Hub nutzt eine SQLModel-Datenbank für Persistenz (konfigurierbar via `DATABASE_URL`).

- Logs werden aktuell gepollt; ein SSE-Endpoint (`/events`) ist optional und kann später ergänzt werden.

- Das Angular-Frontend erwartet CORS-freigeschaltete Agenten und nutzt je Agent den passenden Token.

## Sicherheit & Authentifizierung

Ananta implementiert ein zweistufiges Sicherheitsmodell zur Absicherung der API:

1. **System-Token (`AGENT_TOKEN`)**: 
   - Ein statischer oder dynamisch rotierter Token, der vollen Admin-Zugriff auf den Agenten gewährt.
   - Muss als Bearer-Token im `Authorization`-Header gesendet werden: `Authorization: Bearer <AGENT_TOKEN>`.
   - Alternativ kann er als Query-Parameter `?token=<AGENT_TOKEN>` übergeben werden.
   - Wenn `AGENT_TOKEN` nicht gesetzt ist, läuft der Agent im unsicheren Modus (nicht empfohlen!).

2. **Benutzer-Authentifizierung (JWT)**:
   - Benutzer loggen sich ein und erhalten einen JWT, der mit dem `SECRET_KEY` der Applikation signiert ist.
   - Der JWT enthält Benutzerinformationen und Rollen (z.B. `admin`, `user`).
   - Schützt Endpunkte basierend auf der Benutzerrolle.

### Middleware & Decorators (Python/Flask)

Für die Absicherung eigener Endpunkte stehen folgende Decorators in `agent/auth.py` bereit:

- `@check_auth`: Prüft entweder den `AGENT_TOKEN` oder einen gültigen Benutzer-JWT. Erlaubt Zugriff, wenn einer von beiden gültig ist.
- `@check_user_auth`: Erfordert zwingend einen gültigen Benutzer-JWT (signiert mit `SECRET_KEY`).
- `@admin_required`: Erlaubt Zugriff nur für Admins (entweder via `AGENT_TOKEN` oder Benutzer-JWT mit `role: admin`).

**Beispiel:**

```python
from agent.auth import check_auth, admin_required

@app.route('/api/protected')
@check_auth
def protected_route():
    return jsonify({"message": "Zugriff erlaubt"})

@app.route('/api/admin-only')
@admin_required
def admin_route():
    return jsonify({"message": "Willkommen Admin"})
```

### Authentifizierungs-Flows

1. **System-zu-System (Hub -> Worker)**:
   - Der Hub nutzt den beim Assignment hinterlegten Token des Workers.
   - Request-Header: `Authorization: Bearer <AGENT_TOKEN>`.

2. **Benutzer-Login (Frontend -> Backend)**:
   - POST `/api/auth/login` mit Credentials.
   - Server antwortet mit einem JWT (signiert mit `SECRET_KEY`).
   - Frontend speichert JWT und sendet ihn bei Folgeanfragen im Header mit.

### Token-Rotation

Der Agent unterstützt die automatische Rotation des `AGENT_TOKEN` via `rotate_token()`. Dabei wird der neue Token auch mit dem konfigurierten Hub synchronisiert.

## Hinweise

- CORS ist aktiviert, damit das Angular-Frontend direkt gegen den Agenten sprechen kann.

- Schreibende Endpunkte verlangen bei gesetztem `AGENT_TOKEN` einen `Authorization: Bearer <token>` Header.

## Troubleshooting LLM-Verbindung

Falls Fehlermeldungen wie `Connection refused` bei `host.docker.internal` auftreten:

- **Automatischer Fix**: Führen Sie das Skript **`setup_host_services.ps1`** im Hauptverzeichnis des Projekts mit PowerShell aus (Rechtsklick -> Mit PowerShell ausführen). Dies erledigt die Firewall- und Proxy-Konfiguration für Sie.

- **Ollama**: Muss auf `0.0.0.0` lauschen. Setzen Sie `OLLAMA_HOST=0.0.0.0` bevor Sie Ollama starten.

- **LMStudio**: In Version 0.3.x findet sich dies unter dem Icon `<->` (Local Server) -> Network Settings -> Schalter "Im lokalen Netzwerk bereitstellen".

- Siehe detaillierte Anleitung in `docs/INSTALL_TEST_BETRIEB.md`.

