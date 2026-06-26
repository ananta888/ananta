# Ananta UI & Konfigurations-Guide

> Automatisch generiert aus dem Ananta-Quellcode.
> Zeigt Navigation, UI-Elemente, Chat-Sessions und API-Endpoints.

## Hauptnavigation

### Arbeiten
- **AI Chats** → `/chats`
- **Arbeitsbereich** → `/workspace`
- **Aufgaben** → `/board`
- **CodeHug** → `/codehug`
- **Ergebnisse** → `/artifacts`
- **Hilfe** → `/help`
- **LLM Runtime** → `/llama-runtime`
- **Vorlagen** → `/templates`
- **Voxtral Offline** → `/voxtral-offline`
- **Wikipedia** → `/wikipedia` _Experte_
- **Wissen** → `/knowledge` _Experte_

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
- **Three-Way Diff** → `/diff3` _Experte_
- **Worker Loop Diagnostik** → `/worker-loop-diagnostics` _Experte_
- **Worker Pool** → `/worker-pool` _Experte_

### Konfiguration
- **Admin-Diagnose** → `/admin-diagnostics` _Experte, Admin_
- **Audit-Logs** → `/audit-log` _Experte, Admin_
- **Benutzerverwaltung** → `/user-management` _Experte, Admin_
- **Blueprint-Konfig** → `/blueprint-config` _Experte_
- **Effective Workflow** → `/effective-workflow` _Experte_
- **Einstellungen** → `/settings` _Experte_
- **Instruction Layers** → `/instruction-layers` _Experte_
- **Konfig-Graph** → `/config-graph` _Experte_
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
  - `chat.read-only-banner` — 🐍 Read-only Log-Session — wird ausschließli
  - `chat.retrieval-profile` — Retrieval-Profil
  - `chat.settings-tab` — Einstellungen
  - `chat.system-prompt` — System-Prompt

**snake.**
  - `snake.tab-ai-snake` — AI-Snake
  - `snake.tab-chat` — Chat
  - `snake.tab-explain` — 🔲 Erklären
  - `snake.tab-mode` — Modus
  - `snake.tab-pair` — Pair Dev
  - `snake.tab-sessions` — Sessions
  - `snake.tab-settings` — Einstell.
  - `snake.tab-trace` — Trace

**teams.**
  - `teams.blueprint-catalog` — Standard-Blueprint-Katalog
  - `teams.blueprint-new` — Neu
  - `teams.tab-blueprints` — Blueprints
  - `teams.tab-instantiate` — Teams aus Blueprint

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
- ⚙️ **Ananta-Konfig** (`ananta-settings`): Du bist UI- und Konfigurations-Guide für Ananta. Du hast Zugriff auf Tools, die du aktiv nutzen sollst.  TOOL-REGELN (WI…
- 🐍 **Visual Snake Log** (`ananta-visual`): Read-only Log-Session für die visuelle AI-Snake. Eingehend: [ui-tick] System-Messages mit kompaktem UI-Snapshot der aktu…

## Feature-Dokumentation (docs/)

Folgende Dokumentationsdateien beschreiben die wichtigsten Features:

### API
- **API: Goal Effective Config** (`api-goal-effective-config.md`): ```
- **DOC-GOAL-802: Goal and Plan API reference (short)** (`api-goal.md`): Endpoints
- **Hub API** (`hub-api.md`): Placeholder for the hub API overview.

### Auto-Planner
- **Auto-Planner Guide** (`auto-planner-guide.md`): Der Auto-Planner ist ein LLM-gestütztes System zur automatischen Task-Generierung aus High-Level-Zielen (Goals).

### Blueprint
- **Blueprint- und Rollen-Template-Admin** (`blueprint-admin.md`): Diese Notiz beschreibt den aktuellen Admin-Ist-Zustand fuer Blueprints, Rollen-Templates (API: `templates`) und blueprin
- **Blueprint bundle import/export** (`blueprint-bundle-import-export.md`): This runbook documents the operator flow for transporting blueprint configurations as JSON bundles.
- **Rollout fuer Blueprint- und Template-Migrationen** (`blueprint-migration-rollout.md`): Diese Anleitung beschreibt den sicheren Rollout der aktuellen Blueprint-/Template-Haertungen in produktionsnahen Umgebun
- **Blueprint Product Model (Standard Mode)** (`blueprint-product-model.md`): Dieses Dokument ist der kurze Produktleitfaden fuer normale Nutzer. Es erklaert nur die drei Kernbegriffe und den empfoh
- **Advanced Studio Roadmap (separat vom Standard-UX)** (`blueprint-studio-roadmap.md`): Dieses Dokument sammelt weiterfuehrende Studio-/Admin-Ideen getrennt vom kompakten Produktweg.
- **Standard Blueprints** (`standard-blueprints.md`): Die folgende Liste ist der offizielle Standard-Blueprint-Katalog fuer den produktnahen Einstieg.

### CodeCompass
- **CodeCompass Agent Runtime Instructions** (`codecompass-agent-runtime-instructions.md`): This document is the authoritative reference for how Ananta agents (ananta-worker,
- **CodeCompass Architecture Query Engine** (`codecompass-architecture-query-engine.md`): Status: implementiert (CCAQE-Track, 2026-06-10)
- **CodeCompass Domain Discovery** (`codecompass-domain-discovery.md`): Track: `todos/todo.codecompass-domain-discovery.json` (CCDD)
- **CodeCompass Graph Viewer** (`codecompass-graph-viewer.md`): Angular feature for visualizing CodeCompass static-analysis graphs. The viewer is renderer-independent: a canonical `Gen
- **CodeCompass Relevant-Snippet Handoff** (`codecompass-relevant-snippet-handoff.md`): Dieser Artikel beschreibt, wie Ananta CodeCompass-Kontext aus `rag_helper/research-context.json` an den ananta-worker Pr
- **CodeCompass Retrieval Profile & Source Policy** (`codecompass-retrieval-profile-source-policy.md`): Dieses Dokument beschreibt den vollständigen AI-Snake-Retrieval-Flow: vom Nutzer-Prompt bis zum grounded Prompt an den W
- **CodeCompass Runtime Domain Scope (CCRDS)** (`codecompass-runtime-domain-scope.md`): Status: implementiert (Retrieval-Scope aktiv nutzbar, Write-Scope als
- **CodeCompass** (`codecompass.md`): Placeholder for the CodeCompass overview.

### Instruction Layers
- **Instruction Layer Authoring Guide** (`instruction-layer-authoring-guide.md`): This guide explains how to write safe and useful `user_profile` and `task_overlay` prompts.
- **Instruction Layer Golden Path** (`instruction-layer-golden-path.md`): This is the canonical flow for combining a persistent profile with a scoped overlay.
- **Instruction Layer Model (UPT)** (`instruction-layer-model.md`): This document defines the instruction stack for user profile prompts and task overlays.
- **Instruction Layer Rollout Plan** (`instruction-layer-rollout-plan.md`): This rollout introduces profile and overlay layers additively without breaking existing task flows.

### LLM / Routing
- **LLM Observability: UI Concept** (`llm-observability.md`): Each task proposal stores an `llm_call_profile` list inside `last_proposal.cli_result`. This profile is also forwarded t
- **LLM Provider Config** (`llm-provider-config.md`): Placeholder for LLM provider configuration docs.
- **LLM Routing** (`llm-routing.md`): Placeholder for LLM routing documentation.
- **Ollama-Modellrouting fuer Hub, Scrum-Rollen und OpenCode-Worker** (`ollama-model-routing.md`): Diese Uebersicht beschreibt die **aktuell im Projekt sichtbaren Ollama-Modelle** und leitet daraus eine sinnvolle Nutzun

### Pair Dev / Sharing
- **Operator TUI Shared Sessions** (`operator-tui-shared-sessions.md`): This document explains how to use **OIDC + Device Key + Share Session** in the Operator TUI.

### Policies
- **Context Access Policy Backend Documentation** (`context_access_policy_backend.md`): The Context Access Policy (CAP) backend provides a granular, multi-layered security system for controlling access to con
- **Planning-Agent Governance Contract** (`planning-agent-governance.md`): This document captures the active guardrails for delegated planning.

### Vorlagen / Templates
- **Template Authoring Guide** (`template-authoring-guide.md`): This guide is for writing and maintaining prompt templates in Ananta.
- **Template, Role, Overlay, and Evolver Architecture** (`template-role-overlay-architecture.md`): Status: Working architecture baseline.
- **Template Variable Migration Notes** (`template-variable-migration-notes.md`): This note documents the transition from legacy/flat template variables to the canonical registry model.
- **Template Variable Registry and Runtime Contract** (`template-variable-registry.md`): This note documents the current template variable model, runtime rendering contract, and integration touchpoints across 

### Worker
- **Worker Contract** (`worker-contract.md`): Placeholder for the worker contract.
- **Worker Directory** (`worker-directory.md`): Placeholder for the worker directory overview.
- **Worker Extension Implementation Guide** (`worker-extension-guide.md`): This guide explains how to extend or integrate with the Ananta governed worker execution layer.
- **Worker Capability Routing & Policy Explainability** (`worker-routing-policy-explainability.md`): This document describes how the hub selects workers while preserving the hub–worker architecture constraints.

## Hub-API Endpoints (Auswahl)

**blueprint_routes.py**
  - `GET /teams/blueprints`
  - `POST /teams/blueprints`
  - `GET /teams/blueprints/<blueprint_id>`
  - `PATCH /teams/blueprints/<blueprint_id>`
  - `DELETE /teams/blueprints/<blueprint_id>`
  - `GET /teams/blueprints/<blueprint_id>/bundle`
  - `POST /teams/blueprints/<blueprint_id>/instantiate`
  - `GET /teams/blueprints/<blueprint_id>/work-profile`
  - `GET /teams/blueprints/catalog`
  - `POST /teams/blueprints/import`

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
  - `GET /api/codecompass/self-graph`
  - `GET /api/codecompass/self-graph/domains`

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
  - `GET /api/diff3/sessions/<session_id>/panels/<panel_id>/content`
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
  - `GET /snakes/<snake_id>/events/stream`
  - `PUT /snakes/<snake_id>/ui-state`

**snapshot_diff_api.py**
  - `POST /api/snapshot/diff`

**teams.py**
  - `PATCH /teams/<team_id>`
  - `DELETE /teams/<team_id>`
  - `POST /teams/<team_id>/activate`
  - `GET /teams/<team_id>/blueprint-diff`
  - `GET /teams/roles`
  - `POST /teams/roles`
  - `DELETE /teams/roles/<role_id>`
  - `POST /teams/setup-scrum`
  - `GET /teams/types`
  - `POST /teams/types`
  - `DELETE /teams/types/<type_id>`
  - `POST /teams/types/<type_id>/roles`
  - `GET /teams/types/<type_id>/roles`
  - `PATCH /teams/types/<type_id>/roles/<role_id>`
  - `DELETE /teams/types/<type_id>/roles/<role_id>`

**wiki_graph.py**
  - `GET /api/wiki-graph/article-content`
  - `POST /api/wiki-graph/build`
  - `POST /api/wiki-graph/build-content`
  - `POST /api/wiki-graph/build-domains`
  - `GET /api/wiki-graph/content-status`
  - `GET /api/wiki-graph/domain-articles`
  - `GET /api/wiki-graph/domain-graph`
  - `GET /api/wiki-graph/domain-status`
  - `GET /api/wiki-graph/domains`
  - `GET /api/wiki-graph/expand`
  - `GET /api/wiki-graph/search`
  - `GET /api/wiki-graph/status`
