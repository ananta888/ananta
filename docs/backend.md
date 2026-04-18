# Backend Dokumentation

Dieses Dokument beschreibt Architektur, Datenmodelle und API-Grundlagen des Backends.

## Architekturueberblick
- API-Layer: `agent/ai_agent.py`
- Routen: `agent/routes/`
- Persistenz/Repositories: `agent/database.py`, `agent/repository.py`, `agent/db_models.py`
- Auth: `agent/auth.py`
- Integrations-Registry: `agent/services/integration_registry_service.py` (Provider, Execution-Backends, Exposure-Adapter)

## Kern-Endpoints
- System: `/health`, `/ready`, `/metrics`, `/stats`, `/register`
- Tasks: `/tasks`, `/tasks/{id}`, `/tasks/{id}/assign`, `/tasks/{id}/step/propose`, `/tasks/{id}/step/execute`, `/tasks/{id}/logs`
- Auth (kanonisch): `/login`, `/refresh-token`, `/me`, `/change-password`, `/users/*`, `/mfa/*`
- SGPT/CLI: `/api/sgpt/execute`, `/api/sgpt/backends`, `/api/sgpt/context`
- SGPT Stateful Sessions: `/api/sgpt/sessions`, `/api/sgpt/sessions/{id}`, `/api/sgpt/sessions/{id}/turn`
- Terminal (WebSocket): `/ws/terminal?mode=interactive|read&token=<jwt-or-agent-token>&forward_param=<optional>`

## Runtime- und Pipeline-Metadaten

- `GET /providers/catalog` bildet jetzt neben LM Studio auch weitere konfigurierte `local_openai_backends` ab.
- `POST /llm/generate` kann ohne explizite Modellwahl benchmark-basiert auf ein verfuegbares Modell routen.
- `GET /api/sgpt/backends` liefert fuer CLI-Backends jetzt zusaetzlich `verify_command` sowie konfigurierte lokale OpenAI-kompatible Runtimes.
- `GET /api/sgpt/backends` zeigt ausserdem `cli_session_mode` und `cli_session_runtime`.
- `POST /api/sgpt/execute`, `POST /tasks/{id}/step/propose` und `POST /tasks/{id}/step/execute` geben eine explizite `pipeline` mit Stages und `trace_id` zurueck.
- Task-Propose kann optional dieselbe CLI-Session ueber mehrere Turns fortsetzen (`routing.session_mode=stateful`, `routing.session_id`).

## OpenAI-kompatible Exposition (Hub)

- Endpunkte: `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/responses`, `GET/POST /v1/files`, `GET /v1/ananta/capabilities`
- Alle OpenAI-Compat-Endpunkte sind ueber das effektive Plattform-Governance-Modell kontrolliert: `platform_mode` liefert Defaults, `exposure_policy.openai_compat` kann diese explizit ueberschreiben.
- Self-Loop- und Hop-Guards werden ueber `X-Ananta-Instance-ID` und `X-Ananta-Hop-Count` fail-closed erzwungen.
- Empfohlener Betriebsmodus:
  - `enabled=true`
  - `require_admin_for_user_auth=true`
  - `instance_id` eindeutig pro Instanz setzen
  - `max_hops` konservativ halten (z.B. `3`)
  - `allow_files_api` nur bei Bedarf aktiv
- Policy-Sichtbarkeit:
- `GET /governance/policy` liefert das maschinenlesbare effektive Governance-Read-Model.
- `GET /assistant/read-model` unter `settings.summary.governance.exposure_policy` und `settings.summary.governance.platform_governance`
- `GET /dashboard/read-model` unter `llm_configuration.exposure`
- OpenAI-Compat Responses koennen additive Conversation-Metadaten transportieren (`conversation_id`/`session_id` Echo + `turn_id`), ohne bestehende Clients zu brechen.

## MCP-Exposition (additiv)

- Endpunkte: `GET /v1/mcp/capabilities`, `POST /v1/mcp`
- Der MCP-Pfad bleibt additiv zur OpenAI-Compat-Exposition und nutzt dieselben Hub-Services statt Worker-direkter Steuerung.
- Alle MCP-Aufrufe sind ueber das effektive Plattform-Governance-Modell fail-closed kontrolliert (`platform_mode` plus `exposure_policy.mcp` mit `enabled`, `allow_agent_auth`, `allow_user_auth`, `require_admin_for_user_auth`).
- Erste erlaubte JSON-RPC-Methoden:
  - `tools/list`
  - `tools/call`
  - `resources/list`
  - `resources/read`
- Erste Hub-owned Tools/Resources:
  - Tools: `health.get`, `providers.list_models`, `tasks.list`, `tasks.get`, `artifacts.list`, `knowledge.list_collections`, `evolution.providers.list`, `evolution.analyze`, `evolution.proposals.list`
  - Resources: `ananta://system/health`, `ananta://providers/models`, `ananta://tasks/recent`, `ananta://artifacts/list`, `ananta://knowledge/collections`, `ananta://evolution/providers`
- Observability:
  - Jede erfolgreiche Tool-/Resource-Ausfuehrung wird auditierbar protokolliert (`mcp_tool_called`, `mcp_resource_read`).
  - Policy-Blockierungen werden als `mcp_access_blocked` sichtbar.
  - MCP-Responses enthalten additiv `trace_id` zur Korrelation.

## Evolution Rollout

- Rollout-Phasen fuer disabled, analyze-only, controlled-review und spaeteres Apply sind in `docs/evolution-rollout.md` beschrieben.
- Apply bleibt standardmaessig deaktiviert (`evolution.apply_allowed=false`) und ist nur als explizite zweite Ausbaustufe vorhanden.
- Evolution-Metriken werden unter `/metrics` exportiert: `evolution_analyses_total`, `evolution_proposals_total`, `evolution_validations_total`, `evolution_applies_total`, `evolution_operation_duration_seconds`.

## Stateful Session-Modus (CLI)

- Das Session-Modell ist bewusst getrennt von Inference-Providern (SRP): Sessions gehoeren zu Execution-Backends.
- Session-Lifecycle ist explizit:
  - create
  - turn append
  - read/list
  - close
- Task-scoped Nutzung ist policy-gesteuert ueber `cli_session_mode.allow_task_scoped_auto_session`.

## Plattform-Governance-Modi

- `platform_mode` ist der zentrale Betriebsmodus fuer exponierte Hub-Schnittstellen und Hochrisiko-Zugriffe.
- Unterstuetzte Modi:
  - `local-dev`: lokale Entwicklung, OpenAI-Compat bleibt kompatibel aktiv, MCP und Terminal bleiben standardmaessig aus.
  - `trusted-internal`: interne Nutzung mit expliziten Admin-Grenzen; MCP kann aus dem Modus heraus aktiviert werden.
  - `admin-only`: Exposition ist auf Admin-/Agent-Kontexte ausgerichtet; Terminal bleibt trotzdem nur mit explizitem `terminal_policy.enabled=true` nutzbar.
  - `semi-public`: stark eingeschraenkte Exposition, Files API und Agent-Auth fuer OpenAI-Compat sind standardmaessig aus.
- Explizite Einzelpolicies bleiben additiv und ueberschreiben Modus-Defaults:
  - `exposure_policy.openai_compat`
  - `exposure_policy.mcp`
  - `exposure_policy.remote_hubs`
  - `terminal_policy`
- Das Read-Model `GET /governance/policy` zeigt `policy_version`, `platform_mode`, effektive Exposure-/Terminal-Policies und strukturierte Entscheidungen.

## Remote-Ananta als Provider-Typ

- `remote_ananta_backends` in `/config` fuegt explizite OpenAI-kompatible Remote-Hubs als additiven Provider-Typ hinzu.
- Diese Backends erscheinen in `/providers/catalog` mit `capabilities.provider_type=remote_ananta` sowie `instance_id`/`max_hops`.

## Routing-Entscheidungen und Fallbacks

- Routing ist als Entscheidungskette modelliert, ohne die Hub-Control-Plane zu umgehen.
- `GET /providers/catalog` liefert je Provider `routing_decision` mit `policy_version`, Provider-Typ, Remote-Hub-Kennung, Verfuegbarkeit und Begruendung.
- `POST /llm/generate` liefert unter `routing.decision_chain` die Schritte fuer Request-Override, Benchmark-Auswahl, Default-Konfiguration und Runtime-Probe.
- `GET /dashboard/read-model` zeigt unter `llm_configuration.routing_split.decision_chain` dieselbe maschinenlesbare Struktur fuer die aktuelle Laufzeitkonfiguration.
- `routing_fallback_policy` steuert additiv, welche Fallback-Klassen genutzt werden duerfen:
  - `allow_static_providers`
  - `allow_local_backends`
  - `allow_remote_hubs`
  - `allow_stateful_cli`
  - `allow_stateless_generation`
  - `fallback_order`
  - `unavailable_action`
- Remote-Hubs werden nur als verfuegbar fuer Routing markiert, wenn sowohl die Exposure-Governance als auch `routing_fallback_policy.allow_remote_hubs` dies erlauben.

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
- Zugriff ist standardmaessig fail-closed. `terminal_policy.enabled=true` ist erforderlich.
- `terminal_policy.allow_read` und `terminal_policy.allow_interactive` sind getrennte Freigaben; read-only schaltet keine interaktive Shell frei.
- `terminal_policy.require_admin=true` verlangt eine Admin-Rolle im Token bzw. den Agent-Token.
- `terminal_policy.max_session_seconds` und `terminal_policy.idle_timeout_seconds` begrenzen laufende Sessions.
- `terminal_policy.input_preview_max_chars` begrenzt auditierte Input-Previews.
- `terminal_policy.allowed_roles` und `terminal_policy.allowed_cidrs` koennen Terminal-Zugriff zusaetzlich auf Operator-Rollen oder interne Netze einschraenken.
- Fehlerhafte oder nicht passende Rollen-/Netzbedingungen blockieren den Zugriff fail-closed.
- Nachrichtenformat:
  - Server -> Client: JSON Events `{ "type": "ready|output|error", "data": ... }`
  - Client -> Server (interactive): `{ "type": "input", "data": "<text>" }`
- Audit/Logs:
  - Session-Open/Close, blockierte Zugriffe und Input-Previews werden in `data/terminal_log.jsonl` protokolliert.

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

## Redaction und Sensitivitaetskontrolle

- Eine zentrale Redaction-Schicht (`agent/common/redaction.py`) schuetzt sensible Daten in Logs, Audits, Prompt-Bundles und API-Antworten.
- Unterstuetzte Datenklassen (`SensitiveDataClass`): `token`, `secret`, `credential`, `path`, `internal_url`, `private_prompt`, `ip_address`.
- Sichtbarkeitsstufen (`VisibilityLevel`):
  - `PUBLIC` (0): Maximale Maskierung (standard fuer nicht authentifizierte Zugriffe).
  - `USER` (1): Standard fuer authentifizierte Benutzer; Secrets/Tokens sind maskiert.
  - `ADMIN` (2): Admins duerfen Pfade und interne URLs sehen.
  - `DEBUG` (3): Keine Maskierung (nur fuer lokale Entwicklung).
- Die Redaction erfolgt automatisch in `api_response` basierend auf dem `g.user` Kontext.
- In `agent/common/logging.py` und `agent/common/audit.py` ist die Redaction fest integriert.
- Prompt-Bundles im `ContextBundleService` nutzen ebenfalls die zentrale Redaction, um Leckagen in Richtung LLM-Provider zu minimieren.

## Verwandte Dokus
- API-Spezifikation: `api-spec.md`
- Agent-Setup: `agent/README.md`
- Lokale LLM-/CLI-Strategie: `docs/local-llm-cli-strategy.md`
- DeerFlow-Research-Backend: `docs/deerflow-integration.md`
