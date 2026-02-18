# Architekturplan fuer Ananta

Dieses Dokument beschreibt die Hub-Worker-Architektur und die zentralen Laufzeitpfade.

## Komponenten
- Hub (ROLE=hub): Registry, Task-Orchestrierung, Team- und Template-Verwaltung
- Worker (ROLE=worker): LLM-Integration, Kommandoausfuehrung, Log-Reporting
- Frontend (Angular 21): Dashboard fuer Steuerung und Monitoring

## Neue Komponenten (v0.7+)

### Auto-Planner
- **Zweck**: Automatische Task-Generierung aus High-Level-Goals
- **Integration**: LLM-basierte Planung mit Template-Support
- **Datei**: `agent/routes/tasks/auto_planner.py`
- **API**: `/tasks/auto-planner/*`
- **Details**: `docs/auto-planner-guide.md`

### Trigger-System
- **Zweck**: Externe Integration via Webhooks
- **Unterstützte Quellen**: GitHub, Slack, Jira, Generic
- **Sicherheit**: Rate-Limiting, IP-Whitelist, HMAC-Signaturen
- **Datei**: `agent/routes/tasks/triggers.py`
- **API**: `/triggers/*`
- **Details**: `docs/webhook-integration.md`

## Autonomer Workflow

```
┌─────────────────┐
│ Externe Quelle  │ (GitHub/Slack/Jira/Webhook)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Trigger Engine  │ Empfaengt Events, erstellt Tasks
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Auto-Planner   │ Analysiert Goals, generiert Subtasks
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Autopilot    │ Verarbeitet Tasks automatisch
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Followup-Check  │ Erkennt neue Folgeaufgaben
└────────┬────────┘
         │
         └──────► Zurück zu Trigger (Loop)
```

## Datenfluesse
1. Worker registrieren sich am Hub (`POST /register`).
2. Tasks werden ueber den Hub angelegt und zugewiesen.
3. Propose/Execute laeuft lokal oder via Forwarding an zugewiesene Worker.
4. Logs werden taskbezogen gesammelt und angezeigt.
5. Webhooks erstellen automatisch neue Tasks.
6. Auto-Planner zerlegt Goals in Subtasks.
7. Followups werden nach Task-Abschluss erkannt.

## Technologie-Stack
- Backend: Python 3.11+, Flask, SQLModel
- Frontend: Angular 21
- Persistenz: PostgreSQL/SQLite
- Queue/Cache: Redis (Compose Standard)
- LLM: LMStudio, Ollama, OpenAI, Anthropic

## Referenzen
- Backend-Doku: `docs/backend.md`
- UML-Diagramme: `architektur/uml/`
- Auto-Planner: `docs/auto-planner-guide.md`
- Webhooks: `docs/webhook-integration.md`