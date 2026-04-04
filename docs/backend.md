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
- Auth (kanonisch): `/login`, `/refresh-token`, `/me`, `/change-password`, `/users/*`, `/mfa/*`
- SGPT/CLI: `/api/sgpt/execute`, `/api/sgpt/backends`, `/api/sgpt/context`
- Terminal (WebSocket): `/ws/terminal?mode=interactive|read&token=<jwt-or-agent-token>&forward_param=<optional>`

## Runtime- und Pipeline-Metadaten

- `GET /providers/catalog` bildet jetzt neben LM Studio auch weitere konfigurierte `local_openai_backends` ab.
- `POST /llm/generate` kann ohne explizite Modellwahl benchmark-basiert auf ein verfuegbares Modell routen.
- `GET /api/sgpt/backends` liefert fuer CLI-Backends jetzt zusaetzlich `verify_command` sowie konfigurierte lokale OpenAI-kompatible Runtimes.
- `POST /api/sgpt/execute`, `POST /tasks/{id}/step/propose` und `POST /tasks/{id}/step/execute` geben eine explizite `pipeline` mit Stages und `trace_id` zurueck.

## OpenAI-kompatible Exposition (Hub)

- Endpunkte: `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/responses`, `GET/POST /v1/files`, `GET /v1/ananta/capabilities`
- Alle OpenAI-Compat-Endpunkte sind ueber `exposure_policy.openai_compat` kontrolliert.
- Empfohlener Betriebsmodus:
  - `enabled=true`
  - `require_admin_for_user_auth=true`
  - `allow_files_api` nur bei Bedarf aktiv
- Policy-Sichtbarkeit:
  - `GET /assistant/read-model` unter `settings.summary.governance.exposure_policy`
  - `GET /dashboard/read-model` unter `llm_configuration.exposure`

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
- User-JWT: Login ueber `/login`, Refresh ueber `/refresh-token`
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

### Blueprint-First Teams
- `team_blueprints`: wiederverwendbare Teamvorlagen mit `name`, `description`, optionalem `base_team_type_name` und Seed-Kennzeichnung.
- `blueprint_roles`: strukturierte Rollen eines Blueprints inkl. Sortierung, Pflichtflag und optionalem Template.
- `blueprint_artifacts`: Start-Artefakte eines Blueprints. `kind=task` wird bei der Instanziierung in konkrete `tasks` ueberfuehrt.
- `teams.blueprint_id` und `teams.blueprint_snapshot`: referenzieren den Ursprung und frieren die verwendete Blueprint-Definition fuer die konkrete Teaminstanz ein.
- `team_members.blueprint_role_id`: macht nachvollziehbar, welche Blueprint-Rolle ein Agent in der Instanz abdeckt.

### Team API
- `GET /teams/blueprints`: liefert Seed- und benutzerdefinierte Blueprints inkl. Rollen und Artefakten.
- `POST /teams/blueprints`: erstellt einen neuen Blueprint.
- `PATCH /teams/blueprints/{id}` und `DELETE /teams/blueprints/{id}`: pflegen bestehende Blueprints.
- `POST /teams/blueprints/{id}/instantiate`: materialisiert aus einem Blueprint ein Team, erzeugt Rollenlinks und initiale Artefakte.
- `POST /teams/setup-scrum`: Legacy-Shortcut, delegiert intern jetzt an den Seed-Blueprint `Scrum`.

## Verwandte Dokus
- API-Spezifikation: `api-spec.md`
- Agent-Setup: `agent/README.md`
- Lokale LLM-/CLI-Strategie: `docs/local-llm-cli-strategy.md`
- DeerFlow-Research-Backend: `docs/deerflow-integration.md`
