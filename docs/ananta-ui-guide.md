# Ananta UI & Konfigurations-Guide

> Automatisch generiert aus dem Ananta-Quellcode.
> Zeigt Navigation, UI-Elemente, Chat-Sessions und API-Endpoints.

## Hauptnavigation

### Arbeiten
- **AI Chats** → `/chats`
- **Arbeitsbereich** → `/workspace`
- **Aufgaben** → `/board`
- **Ergebnisse** → `/artifacts`
- **Hilfe** → `/help`
- **LLM Runtime** → `/llama-runtime`
- **Vorlagen** → `/templates`
- **Voxtral Offline** → `/voxtral-offline`

### Automatisierung
- **Auto-Planner** → `/auto-planner` _Experte_
- **Webhooks** → `/webhooks` _Experte_

### Betrieb
- **Agenten** → `/agents` _Experte_
- **Archiv** → `/archived` _Experte_
- **Dashboard** → `/dashboard` _Experte_
- **Goal Artifacts** → `/goal-artifacts` _Experte_
- **Graph** → `/graph` _Experte_
- **Operationen** → `/operations` _Experte_
- **Sources** → `/sources` _Experte_
- **Strategy Game Demo** → `/strategy-game-demo` _Experte_
- **Worker Loop Diagnostik** → `/worker-loop-diagnostics` _Experte_
- **Worker Pool** → `/worker-pool` _Experte_

### Konfiguration
- **Admin-Diagnose** → `/admin-diagnostics` _Experte, Admin_
- **Audit-Logs** → `/audit-log` _Experte, Admin_
- **Benutzerverwaltung** → `/user-management` _Experte, Admin_
- **Einstellungen** → `/settings` _Experte_
- **Instruction Layers** → `/instruction-layers` _Experte_
- **Mobile Shell** → `/mobile-shell` _Experte_
- **Policy** → `/context-access-policy` _Experte, Admin_
- **Python Runtime** → `/python-runtime` _Experte_
- **Rollenänderungen** → `/role-audit` _Experte, Admin_
- **Teams** → `/teams` _Experte_

## Control Center (Unterseiten)

Erreichbar über `/control-center` → linkes Menü:
- `control-center` — Shell
- `dashboard` — Dashboard
- `tasks` — TaskBoard
- `sessions` — Sessions
- `artifacts` — ArtifactBrowser
- `workers` — Workers
- `policies` — PolicyApproval
- `codecompass` — Placeholder

## UI-Elemente (Waypoints)

Diese Bezeichner identifizieren konkrete Schaltflächen/Bereiche in der Oberfläche:

**assistant.**
  - `assistant.snake-chat-btn` — 💬 Snake Chat
  - `assistant.tab-ai-snake` — AI-Snake
  - `assistant.tab-chat` — Chat
  - `assistant.tab-mode` — Modus
  - `assistant.tab-pair-dev` — Pair Dev
  - `assistant.tab-settings` — Einstellungen

**cc.**
  - `cc.artifacts` — Artifacts
  - `cc.codecompass` — CodeCompass
  - `cc.dashboard` — Dashboard
  - `cc.policies` — Policies
  - `cc.sessions` — Sessions
  - `cc.tasks` — Tasks
  - `cc.workers` — Workers

**chat.**
  - `chat.backend-select` — Backend
  - `chat.messages-tab` — Nachrichten ({{ messageCount() }})
  - `chat.new-session` — ＋
  - `chat.retrieval-profile` — Retrieval-Profil
  - `chat.settings-tab` — Einstellungen
  - `chat.system-prompt` — System-Prompt

**nav.**
  - `nav.` — (dynamisch)

**snake.**
  - `snake.tab-ai-snake` — AI-Snake
  - `snake.tab-chat` — Chat
  - `snake.tab-mode` — Modus
  - `snake.tab-pair` — Pair Dev
  - `snake.tab-sessions` — Sessions
  - `snake.tab-settings` — Einstellungen
  - `snake.tab-trace` — Trace

## Chat-Sessions (Typen)

### Allgemein
- 💻 **Code-Help** (`code-help`): You are a focused code assistant for the Ananta project. When answering, prefer concrete file paths, function names, and…
- ✍️ **Schreib-Coach** (`writing-coach`): You are a writing coach. Help the user clarify their thinking, structure their arguments, and improve their prose. Do no…
- 💬 **Allgemein** (`general`): You are a helpful, friendly AI assistant. Use the project's CodeCompass context when it seems relevant, but don't force …

### Architektur
- 🏗️ **Architektur-Überblick** (`arch-overview`): Du bist Architekt des Ananta-Projekts. Der Nutzer beschreibt welchen Teil des Systems er visualisieren will. Antworte IM…
- 🔷 **Klassen & Interfaces** (`arch-classes`): Du bist Architekt. Der Nutzer nennt einen Bereich oder eine Komponente. Antworte IMMER mit einem Mermaid classDiagram. Z…
- ↔️ **Sequenz & Abläufe** (`arch-sequence`): Du bist Architekt. Der Nutzer beschreibt einen Ablauf oder Prozess. Antworte IMMER mit einem Mermaid sequenceDiagram. Ve…
- 🔗 **Abhängigkeiten** (`arch-deps`): Du bist Architekt. Der Nutzer nennt ein Modul oder eine Komponente. Antworte IMMER mit einem Mermaid graph LR Diagramm. …

### Konfiguration
- ⚙️ **Ananta-Konfig** (`ananta-settings`): Du bist UI-Konfigurations-Guide für Ananta. Deine einzige Aufgabe: erkläre dem Nutzer SCHRITT FÜR SCHRITT, welche Menüpu…

## Hub-API Endpoints (Auswahl)

**chat.py**
  - `GET /api/chat/sessions`
  - `POST /api/chat/sessions`
  - `GET /api/chat/sessions/<session_id>`
  - `PUT, PATCH /api/chat/sessions/<session_id>`
  - `DELETE /api/chat/sessions/<session_id>`
  - `POST /api/chat/sessions/<session_id>/activate`

**codecompass_domain_scope.py**
  - `POST /api/codecompass/domain-scope/preview`
  - `GET /api/codecompass/domains`

**codecompass_graph.py**
  - `GET /api/codecompass/graph`
  - `GET /api/codecompass/graph/expand`
  - `GET /api/codecompass/graph/node/<node_id>`
  - `GET /api/codecompass/query`

**context_policy.py**
  - `GET /api/context-policy/policies`
  - `POST /api/context-policy/policies`
  - `GET /api/context-policy/policies/<policy_id>/latest`
  - `POST /api/context-policy/validate`

**control_center_api.py**
  - `GET /api/codecompass/context-scopes`
  - `POST /api/codecompass/context-scopes/preview`
  - `GET /api/events/stream`
  - `POST /api/events/stream-token`
  - `GET /api/policies`
  - `POST /api/policy/approve`
  - `GET /api/projects`
  - `GET /api/projects/<project_id>/tasks`
  - `GET /api/sessions`
  - `GET /api/sessions/<session_id>`
  - `POST /api/sessions/<session_id>/cancel`
  - `GET /api/sessions/<session_id>/policy-decisions`
  - `GET /api/sessions/<session_id>/tool-calls`
  - `POST /api/tasks`
  - `GET /api/tasks/<task_id>`
  - `PATCH /api/tasks/<task_id>`
  - `POST /api/tasks/<task_id>/sessions`
  - `GET /api/workers`

**demo.py**
  - `GET /api/demo/preview`

**diff3.py**
  - `POST /api/diff3/sessions`
  - `GET /api/diff3/sessions/<session_id>`
  - `DELETE /api/diff3/sessions/<session_id>`
  - `PUT /api/diff3/sessions/<session_id>/ai/mode`
  - `POST /api/diff3/sessions/<session_id>/ai/run`
  - `PUT /api/diff3/sessions/<session_id>/focus`
  - `PUT /api/diff3/sessions/<session_id>/layout`
  - `PUT /api/diff3/sessions/<session_id>/panels/<panel_id>`
  - `PUT /api/diff3/sessions/<session_id>/sync`

**freecad_client_surface.py**
  - `POST /goals`
  - `GET /health`

**goal_artifacts.py**
  - `GET /goals/<goal_id>/artifacts/citations`
  - `GET /goals/<goal_id>/artifacts/executions/<provenance_id>`
  - `GET /goals/<goal_id>/artifacts/graph`
  - `GET /goals/<goal_id>/artifacts/outputs`
  - `GET /goals/<goal_id>/artifacts/outputs/<output_id>/provenance`
  - `GET /goals/<goal_id>/artifacts/source-candidates`
  - `GET /goals/<goal_id>/artifacts/sources`
  - `POST /goals/<goal_id>/artifacts/sources/<grant_id>/revoke`
  - `POST /goals/<goal_id>/artifacts/sources/grant`

**instruction_layers.py**
  - `POST /goals/<goal_id>/instruction-selection`

**integrations_workflows.py**
  - `POST /api/integrations/workflows/callback`

**network_profiles.py**
  - `GET /api/network-profiles`
  - `GET /api/network-profiles/<profile_id>`

**sgpt.py**
  - `POST /sessions`
  - `GET /sessions`
  - `GET /sessions/<session_id>`
  - `DELETE /sessions/<session_id>`
  - `POST /sessions/<session_id>/turn`

**snakes_config_routes.py**
  - `POST /snakes`
  - `GET /snakes`
  - `DELETE /snakes/<snake_id>`
  - `POST /snakes/<snake_id>/heartbeat`
  - `POST /snakes/<snake_id>/messages`
  - `GET /snakes/<snake_id>/messages`
  - `GET /snakes/participants`

**snakes_execution_routes.py**
  - `POST /snake/ask`
  - `POST /snakes/<snake_id>/chat/ack`
  - `POST /snakes/<snake_id>/chat/cancel`
  - `POST /snakes/<snake_id>/chat/messages`
  - `GET /snakes/<snake_id>/chat/messages`
  - `GET /snakes/<snake_id>/chat/traces`
  - `GET /snakes/<snake_id>/chat/traces/<trace_id>`
  - `GET /snakes/<snake_id>/chat/traces/<trace_id>/events`
