# AI-Agent (Flask API)

Leichter Python‑Agent, der ein Terminal über LLM‑generierte Shell‑Befehle steuert. Der Agent ist ein eigenständiger Flask‑API‑Server und benötigt keinen Controller und keine Datenbank mehr.

## Endpunkte

- GET `/health` → `{ "status": "ok" }`
- GET `/config` / POST `/config` → Agent‑Konfiguration (persistiert in `data/config.json`)
- POST `/step/propose` → LLM schlägt einen Befehl vor (noch ohne Ausführung)
- POST `/step/execute` → führt einen Befehl aus (optional mit `task_id`)
- GET `/logs?limit=&task_id=` → letzte Einträge aus `data/terminal_log.jsonl`

Optionaler Hub‑Modus (`ROLE=hub`):
- Templates: GET/POST/PUT/DELETE `/templates*`
- Tasks: GET/POST/GET/PATCH `/tasks*`, plus `/tasks/{id}/assign|propose|execute|logs`

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

## Hinweise

- CORS ist aktiviert, damit das Angular‑Frontend direkt gegen den Agenten sprechen kann.
- Schreibende Endpunkte verlangen bei gesetztem `AGENT_TOKEN` einen `Authorization: Bearer <token>` Header.
