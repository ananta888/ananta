# Backend Dokumentation

Dieses Dokument beschreibt Architektur, Datenmodelle und API-Grundlagen des Backends.

## Architekturueberblick
- API-Layer: `agent/ai_agent.py`
- Routen: `agent/routes/`
- Persistenz/Repositories: `agent/database.py`, `agent/repository.py`, `agent/db_models.py`
- Auth: `agent/auth.py`

## Kern-Endpoints
- System: `/health`, `/ready`, `/metrics`, `/stats`, `/register`
- Tasks: `/tasks`, `/tasks/{id}`, `/tasks/{id}/assign`, `/tasks/{id}/step/propose`, `/tasks/{id}/step/execute`, `/tasks/{id}/logs`
- Auth: `/api/auth/*`
- SGPT/CLI: `/api/sgpt/execute`, `/api/sgpt/backends`, `/api/sgpt/context`
- Terminal (WebSocket): `/ws/terminal?mode=interactive|read&token=<jwt-or-agent-token>&forward_param=<optional>`

## Route-Inventory / Contract-Check
Zum schnellen Abgleich von Dokumentation und implementierten Routen:

```bash
python devtools/export_route_inventory.py
```

Optional mit Methodenansicht:

```bash
python devtools/export_route_inventory.py --include-methods
```

## Authentifizierung
- System-Token: `AGENT_TOKEN` (Bearer)
- User-JWT: Login ueber `/api/auth/login`, Refresh ueber `/api/auth/refresh-token`
- WebSocket-Handshake fuer `/ws/terminal`:
  - `token` als Query-Parameter (oder optional `Authorization: Bearer ...` im Upgrade-Request)
  - Erlaubt sind AGENT_TOKEN (statisch/JWT) oder User-JWT (`SECRET_KEY`)

## WebSocket Terminal (`/ws/terminal`)
- Modi:
  - `interactive`: startet eine PTY-Shell (`/bin/sh` oder `SHELL_PATH`) und bridged I/O bidirektional.
  - `read`: read-only Stream fuer `data/terminal_log.jsonl` (tail/follow).
- Nachrichtenformat:
  - Server -> Client: JSON Events `{ "type": "ready|output|error", "data": ... }`
  - Client -> Server (interactive): `{ "type": "input", "data": "<text>" }`
- Audit/Logs:
  - Session-Open/Close und Input-Previews werden in `data/terminal_log.jsonl` protokolliert.

## Sicherheitshinweise
- Terminal-Zugriff gibt Shell-Zugang auf den jeweiligen Agent-Container/Host-Kontext.
- Empfohlen:
  - Nur fuer authentifizierte Admins freigeben.
  - Restriktive Container-/OS-Sandboxing-Policies nutzen.
  - Keine Langzeit-Tokens in URLs persistieren oder loggen.

## Datenmodelle
Zentrale Tabellen/Modelle liegen in `agent/db_models.py` (Users, Teams, Tasks, Templates, Audit).

## Verwandte Dokus
- API-Spezifikation: `api-spec.md`
- Agent-Setup: `agent/README.md`
