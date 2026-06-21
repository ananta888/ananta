# CodeHug — Special View

Stand-alone Spezialansicht des Ananta-Frontends fuer Code-Verstehen, Kontext-Bau
und sicheres Vorbereiten von Aenderungen.

## Architektur

- **Hub-Worker-orientiert** — alle Datenoperationen laufen ueber den Hub.
- **Lazy-loaded** Feature-Modul unter `/codehug` (siehe `codehug.routes.ts`).
- **Standalone Components** (Angular 21, OnPush + Signals).
- **BPMN.js** fuer Topologie-Graph (mit SVG-Fallback).
- **Eigener SVG-Graph-Renderer** fuer CodeHug-spezifische Symbol-Graphen.

## Verzeichnisstruktur

```
src/app/features/codehug/
├── codehug.routes.ts                 # Routes: dashboard, context, search, refactoring, agents, custom-agents, internals
├── components/                       # UI-Komponenten
│   ├── codehug-shell.component.ts    # Layout-Root mit Top-Nav und 3-Spalter
│   ├── codehug-dashboard.component.ts
│   ├── codehug-context-builder.component.ts
│   ├── codehug-agents.component.ts
│   ├── codehug-internals.component.ts       # CH-014: Topologie + Trace + Config
│   ├── refactoring-panel.component.ts      # CH-005
│   ├── custom-agent-editor.component.ts    # CH-006
│   ├── search-and-explain.component.ts     # CH-007
│   └── ... (weitere Panel/Sidebar-Components)
├── graph/                            # Visualisierungen
│   ├── topology-graph.component.ts  # BPMN.js + SVG-Fallback
│   ├── dependency-graph.component.ts # SVG Force-Directed
│   └── trace-view.component.ts        # 3-stufige Trace-View (simplified/details/raw)
├── services/
│   ├── code-compass.service.ts        # /api/codecompass/query
│   ├── context-package.service.ts     # Kontext-Pakete
│   ├── policy.service.ts              # CH-010: Policy + Audit + RateLimit + Risk
│   ├── refactoring.service.ts         # CH-005
│   ├── custom-agent.service.ts        # CH-006
│   ├── search.service.ts              # CH-007: Suche + Heuristik/LLM-Explain
│   ├── topology.service.ts            # CH-014: Hub/Worker-Topologie
│   ├── persistence.service.ts         # CH-011: Workspaces + Snapshots
│   └── agent-run.service.ts           # Agent-Runs
├── state/
│   ├── codehug.facade.ts             # Aggregierter Facade
│   └── context-builder.state.ts      # State-Management fuer Kontext-Builder
└── models/
    └── codehug.models.ts              # Alle DTOs
```

## EPIC-Zuordnung

| EPIC | Komponenten | Services | Tests |
|------|-------------|----------|-------|
| CH-001 Routing & Layout | codehug.routes, codehug-shell | — | routing.cover |
| CH-002 Dashboard | codehug-dashboard | facade | — |
| CH-003 Kontext-Builder | codehug-context-builder | context-package, state | context-builder.state.spec |
| CH-004 Code-Verstehen | dependency-graph, code-compass | code-compass | code-compass.service.spec |
| CH-005 Refactoring | refactoring-panel | refactoring | refactoring.service.spec |
| CH-006 Custom Agents | custom-agent-editor | custom-agent | custom-agent.service.spec |
| CH-007 Suche & Erklaerung | search-and-explain | search | search.service.spec |
| CH-008 UI/Layout | codehug-shell | — | shell.cover |
| CH-009 Backend-Anbindung | (alle) | (alle) | service.spec |
| CH-010 Security | (alle) | policy | policy.service.spec, policy.security.spec |
| CH-011 Persistenz | (alle) | persistence | persistence.service.spec |
| CH-012 Tests | — | — | (alle .spec.ts) |
| CH-013 Dokumentation | — | — | diese Datei |
| CH-014 Topologie & Internals | codehug-internals, topology-graph, trace-view | topology | topology.service.spec |

## Sicherheitsmodell

- **Default read-only**. Schreibende Aktionen erfordern `PolicyService.armWriteMode()`.
- **Write-Modus-Timeout** (default 15 min) — `ensureWriteModeValid()` dekativiert bei Ablauf.
- **Tool-Risk-Assessment** — deterministische Vorab-Einschaetzung (low/medium/high/critical).
- **Audit-Log** — alle Policy-Checks werden geloggt (lokal in-memory, 500 Eintraege).
- **Rate-Limit** — Frontend-side Buckets, Backend hat eigene Quota.
- **Fallback-Pattern**: heuristisch (Signatur + JSDoc) → nur bei leeren Daten LLM.

## Konventionen

- **Service-Ergebnisse**: snake_case vom Backend (Hub) wird via normalize-Methoden auf
  camelCase Modelle gemappt. Immer explizite normalize-Schritte im Service.
- **Models**: alle DTOs in `codehug.models.ts` (single source of truth).
- **Tests**: Vitest + `TestBed.configureTestingModule`, Mocks pro Service.
- **3-stufige Patterns**: Trace-View (simplified/details/raw), Write-Mode (read-only/armed/active),
  Risk-Levels (low/medium/high/critical), Explanation (heuristic/llm/hybrid).

## Build & Test

```bash
# Tests
npx vitest run src/app/features/codehug/

# Build (im Frontend-Workspace)
ng build
```

Stand: 91 Tests gruen, alle 14 EPICs implementiert.
