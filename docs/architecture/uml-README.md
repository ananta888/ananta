# Architekturplan fuer Ananta

> Lifecycle status: **active-supporting** (architecture companion).
> Canonical control-plane fallback guardrails live in `docs/hub-fallback-and-reliability.md`.

Dieses Dokument beschreibt die Hub-Worker-Architektur und die zentralen Laufzeitpfade.

## Komponenten
- Hub (ROLE=hub): Registry, Task-Orchestrierung, Team- und Template-Verwaltung
- Worker (ROLE=worker): LLM-Integration, Kommandoausfuehrung, Log-Reporting
- Frontend (Angular 21): Dashboard fuer Steuerung und Monitoring

## Blueprint-First Teammodell
- Primare Konfigurationseinheit ist `TeamBlueprint`: beschreibt ein wiederverwendbares Team-Setup inklusive Rollen, Start-Artefakten und optionalem Basis-Team-Typ.
- `BlueprintRole` definiert die fachliche Soll-Besetzung eines Blueprints. Bei der Instanziierung wird jede Blueprint-Rolle auf eine operative `Role` abgebildet und bei Bedarf an den referenzierten `TeamType` gekoppelt.
- `BlueprintArtifact` beschreibt Startobjekte, die beim Instanziieren materialisiert werden. Aktuell wird `kind=task` in initiale Team-Tasks umgesetzt.
- `Team` ist die konkrete Instanz eines Blueprints. Die Instanz referenziert den Ursprung ueber `blueprint_id` und friert die verwendete Definition in `blueprint_snapshot` ein.
- `TeamMember` bleibt die operative Zuordnung zu Agenten und Rollen, kann aber zusaetzlich ueber `blueprint_role_id` zur Blueprint-Sollrolle zurueckverfolgt werden.
- Overrides sind bewusst auf der Instanzgrenze gehalten: Aktivierungsstatus, Beschreibung, Member-Zuordnung und optionale Custom-Templates werden am Team bzw. Teammitglied gepflegt, nicht im Blueprint selbst.

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
- Zielmodell: `docs/autonomous-platform-target-model.md`
- Backend-Doku: `docs/backend.md`
- UML-Diagramme: `architektur/uml/`
- Auto-Planner: `docs/auto-planner-guide.md`
- Webhooks: `docs/webhook-integration.md`

## Goal-first Erweiterung
- Goal -> Plan -> Task -> Execution -> Verification -> Artifact bleibt hub-gesteuert.
- Der Hub delegiert standardmaessig an Worker und bleibt Eigentuer der Queue, der Planungs- und Verifikationsentscheidungen.
- Hub-as-worker ist nur Fallback. Auch dann bleibt dieselbe Governance- und Auditspur erhalten.
- Ergebnisansichten sind artifact-first. Detailansichten fuer Plan, Policy, Verification und Trace bleiben explizite Drilldowns.

## Neue Diagramme
- `architektur/uml/goal-ingestion-sequence.mmd`: Goal-Aufnahme, Planung, Delegation und Verifikation.
- `architektur/uml/execution-isolation-sequence.mmd`: Container-Grenzen, Workspace-Lease und Cleanup.
