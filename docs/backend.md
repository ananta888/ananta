# Backend Überblick

Dieses Dokument fasst die Backend-Architektur, Datenmodelle und Authentifizierung zusammen.

## Datenhaltung (SQLModel)

Das Backend nutzt SQLModel (Postgres/SQLite) für persistente Daten. Zentrale Tabellen:

- `UserDB`: Benutzer, Rollen, MFA-Status
- `AgentInfoDB`: registrierte Agenten (Hub/Worker)
- `TeamDB`, `TeamTypeDB`, `TeamMemberDB`: Teams, Team-Typen, Mitglieder
- `RoleDB`, `TeamTypeRoleLink`: Rollen und Zuordnungen je Team-Typ
- `TemplateDB`: Prompt-Templates
- `TaskDB`: Tasks inkl. Status, Assignment, Logs-Metadaten
- `AuditLogDB`: Audit-Events für sicherheitsrelevante Aktionen

Zugriff erfolgt über `agent/repository.py` (Repository-Layer) und `agent/db_models.py` (Modelle).

## Authentifizierung & Rechte

Es gibt zwei Token-Typen:

- **Agent-Token (`AGENT_TOKEN`)**: Admin-Token für schreibende Endpunkte.
- **User-JWT (`/login`)**: Benutzer-Token, Rolle `admin` oder `user`.

Regeln (Auszug):

- Schreibende Endpunkte (POST/PUT/PATCH/DELETE) erfordern Admin-Rechte.
- Team-/Rollen-/Template-Management ist Admin-only.
- `/llm/generate` akzeptiert Tool-Calls nur für erlaubte Tools (Allowlist).

## Team/Role/Template Mapping

- Team-Typen definieren erlaubte Rollen (`TeamTypeRoleLink`).
- Pro Rolle kann ein Template zugeordnet werden (`template_id`), das als Default dient.
- Team-Mitglieder können ein eigenes `custom_template_id` setzen.

## LLM-Integration

Ananta unterstützt verschiedene LLM-Provider (lmstudio, ollama, openai, anthropic).

- **Timeout-Steuerung**: Über den Endpunkt `/llm/generate` kann in der `config` ein per-Request `timeout` (in Sekunden) gesetzt werden. Falls kein Wert angegeben wird, greift der globale Fallback `settings.http_timeout` (Standard: 60 Sekunden).
- **Tool-Calling**: Der Agent kann Tools ausführen, sofern diese in der Allowlist stehen und der Benutzer über die erforderlichen Rechte verfügt.

## Logs

Terminal-Logs werden als JSONL in `data/terminal_log.jsonl` gespeichert. Task-Logs werden im Hub aggregiert und über `/tasks/{id}/logs` bereitgestellt.
