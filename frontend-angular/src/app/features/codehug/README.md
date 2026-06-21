# CodeHug — Special View

Stand-alone Spezialansicht des Ananta-Frontends fuer Code-Verstehen, Kontext-Bau
und sicheres Vorbereiten von Aenderungen.

## Architektur

- **Hub-Worker-orientiert** — alle Datenoperationen laufen ueber den Hub.
- **Lazy-loaded** Feature-Modul unter `/codehug` (siehe `codehug.routes.ts`).
- **Standalone Components** (Angular 21, OnPush + Signals).
- **SVG-Canvas-Editor** fuer Topologie (bpmn-js entfernt; Ziel: Node-Editor wie Grafikprogramm).
- **Eigener SVG-Graph-Renderer** fuer CodeHug-spezifische Symbol-Graphen.

## Verzeichnisstruktur

```
src/app/features/codehug/
├── codehug.routes.ts                 # Routes: dashboard, context, search, refactoring, agents, custom-agents, internals, policy
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
│   ├── codehug-canvas.component.ts  # SVG-Canvas-Node-Editor (Drag, Zoom, Pan, Inspector, Run-Highlighting)
│   ├── topology-graph.component.ts  # Minimaler SVG-Fallback (schreibgeschützte Topologie-Übersicht)
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
├── guards/
│   └── codehug-write-mode.guard.ts   # CanActivate-Guard fuer Write-Mode-geschuetzte Routen
├── policy-panel/
│   └── policy-panel.component.ts     # CH-010-004: Inline Policy-Edit-Panel (/codehug/policy)
├── testing/
│   └── codehug-test-utils.ts         # Shared Mock-Factories fuer alle Services
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

Stand: Services + Unit-Tests vollstaendig; E2E-Skeleton unter `tests/codehug/codehug-main.e2e.ts`.

## Developer Notes (CH-013-002)

### Neue Komponenten / Strukturen (2026-06)

| Neu | Beschreibung |
|-----|-------------|
| `guards/codehug-write-mode.guard.ts` | `CanActivateFn` fuer Routen die aktiven Write-Modus erfordern. Leitet auf `/codehug?writeRequired=1` um. |
| `policy-panel/policy-panel.component.ts` | Vollstaendiges Policy-Edit-Panel: allowedPaths, deniedPaths, sensitive-Patterns, Audit-Log. Route: `/codehug/policy`. |
| `testing/codehug-test-utils.ts` | Mock-Factories (`mockProject`, `mockAgentRun`, `mockPolicySnapshot`, ...) fuer alle Services. Verhindert Duplikation in Specs. |
| `tests/codehug/codehug-main.e2e.ts` | Playwright-E2E-Skeleton: Navigation, Dashboard, Context-Builder, Agents, Fehlerszenarien. |

### Drag-and-Drop im Context-Builder

`CdkDropList` / `CdkDrag` via `@angular/cdk/drag-drop`. Jede Datei-Zeile im Datei-Baum ist
ein `cdkDrag`-Item. Die mittlere Paket-Spalte ist ein `cdkDropList` mit id `ch-pkg-drop`.
Drop ruft `state.toggleFile(file.path, true)` auf (inkl. sensitive-Confirm-Logik).

Limitierung: Sensitive-Confirm laeuft noch als `window.confirm` — Ziel ist eine eigene
Modal-Komponente ohne Browser-Dialog.

### Canvas-Node-Editor (implementiert in `graph/codehug-canvas.component.ts`)

Die Internals-Ansicht (`/codehug/internals`) zeigt die Topologie wie ein Grafikprogramm:

- **Node-Typen**: Hub (Amber), Worker-LLM (Blau), Worker-Det (Grau), Test-Layer (Grün, gestapelt),
  Routing-Rule (Teal) — je mit Icon, Label, Sublabel, Badge
- **Drag**: Jeder Node ist frei positionierbar via SVG-MouseDown/Move/Up (document-level)
- **Inspector-Panel**: Click auf Node öffnet rechts einen Inspektor mit allen Feldern
  — für Layer: Toggle enabled/disabled; für Routing-Rules: Backend-Selector
- **Live-Highlighting**: Bei laufendem Agent-Run werden Worker per Glow-Filter markiert
  (`active` = blau pulsierend, `completed` = grün, `failed` = rot)
- **Pan/Zoom**: Hintergrund-Drag für Pan, Mausrad für Zoom; Toolbar mit Zoom±/Reset/Fit-Buttons
- **SVG-Effekte**: Animierte Pulse-Ringe, Glow-Filter (feGaussianBlur), gestapelte Cards für Layer

Offene Erweiterungen:
- Position-Persistenz via Hub-API (aktuell nur in-memory per Session)
- Policy-Layer als eigener Node-Typ (sobald `/api/codehug/policy/current` verfügbar)
- Template-Nodes (ChTaskTemplateReadModel)

### API-Erwartungen gegenueber Hub

Alle Endpunkte unter `/api/codehug/`:
- `GET /api/codehug/policy/current` → `ChPolicySnapshotReadModel`
- `PATCH /api/codehug/policy` + Body `ChPolicyUpdateRequest` → `ChPolicySnapshotReadModel`
- `GET /api/codehug/policy/decisions?limit=N` → `{ decisions: ChPolicyDecisionReadModel[] }`
- `POST /api/codehug/policy/check` → `ChPolicyDecisionReadModel`
- `GET /api/codecompass/projects` → `ChProjectReadModel[]`
- `POST /api/codecompass/resolve_context` → `ChResolveContextResponse`
- `POST /api/codecompass/search_symbols` → `ChSearchSymbolsResponse`
- `POST /api/agent-runs/start` → `{ runId: string }`
- `GET /api/agent-runs/:runId` → `ChAgentRunReadModel`
- `DELETE /api/agent-runs/:runId` → 204

### Offene TODOs

- [ ] Sensitive-Confirm als eigene Modal-Komponente (statt `window.confirm`)
- [ ] Multi-Agent-Orchestrierungs-UI (CH-006-003)
- [x] Canvas-Node-Editor für Internals-View (CH-008-003 / CH-014) — implementiert in `graph/codehug-canvas.component.ts`
- [ ] Canvas: Node-Position-Persistenz via Hub-API
- [ ] Canvas: Policy-Layer + Template als eigene Node-Typen
- [ ] E2E-Mock-Backend für vollstaendige Agent-Flow-Tests (CH-012-004)
- [ ] Live-Apply Hub-Konfiguration (CH-014-005)
