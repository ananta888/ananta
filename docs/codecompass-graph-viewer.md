# CodeCompass Graph Viewer

Angular feature for visualizing CodeCompass static-analysis graphs. The viewer is renderer-independent: a canonical `GenericGraphModel` feeds all view variants (simple list, 2D canvas, 3D WebGL) through a single adapter boundary.

---

## Architecture overview

```
domain_graph_artifact.v1 (API JSON)
        │
        ▼
GraphAdapterService.fromDomainArtifact()
        │
        ▼
GenericGraphModel  ──────────────────────────────────────────────────────┐
        │                                                                 │
        ▼                                                                 │
GraphStateService (signals)                                               │
  • graph()          – full model                                         │
  • filteredNodes()  – after nodeKindFilter + searchText                  │
  • filteredEdges()  – edges whose endpoints survive the node filter      │
  • viewMode()       – 'simple' | '2d' | '3d'                             │
  • selectedNode()   – currently selected node or null                    │
  • selectedEdge()   – currently selected edge or null                    │
        │                                                                 │
        ▼                                                                 │
GraphViewerComponent (shell)                                              │
  • GraphToolbarComponent    – mode switch + filters                      │
  • [viewMode=simple]  →  SimpleGraphViewComponent                        │
  • [viewMode=2d]      →  Graph2dViewComponent (cytoscape, lazy)          │
  • [viewMode=3d]      →  Graph3dViewComponent (3d-force-graph, lazy) ◄──┘
  • GraphDetailPanelComponent – shown when a node or edge is selected
```

Source root: `frontend-angular/src/app/features/codecompass-graph/`

---

## GenericGraphModel

Defined in `models/graph.model.ts`.

```typescript
export interface GraphNode {
  id:       string;   // from domain artifact node_id
  kind:     GraphNodeKind;
  label:    string;   // from attributes.name
  file:     string;   // from attributes.file
  content:  string;   // from attributes.content
  recordId: string;   // from attributes.record_id
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  id:       string;   // "<source>→<target>:<edgeType>"
  source:   string;   // source node id
  target:   string;   // target node id
  edgeType: GraphEdgeType;
  confidence: number; // 0–1 from attributes.confidence
  metadata: Record<string, unknown>;
}

export interface GenericGraphModel {
  nodes:    GraphNode[];
  edges:    GraphEdge[];
  metadata: Record<string, unknown>;
  warnings: string[];
}
```

### Node kinds

```typescript
export type GraphNodeKind =
  | 'java_method'
  | 'java_type'
  | 'config'
  | 'xml_tag'
  | 'unknown';
```

Any unrecognized `node_type` from the backend is mapped to `'unknown'`.

### Edge types

```typescript
export type GraphEdgeType =
  | 'calls_probable_target'
  | 'injects_dependency'
  | 'field_type_uses'
  | 'extends'
  | 'implements'
  | 'child_of_type'
  | 'child_of_file'
  | 'declares_method'
  | 'declares_bean'
  | 'transactional_boundary'
  | 'jpa_relation'
  | 'related';
```

Any unrecognized `relation` from the backend is mapped to `'related'`.

---

## Mapping: domain_graph_artifact.v1 → GenericGraphModel

| domain artifact field        | GenericGraphModel field      | notes                                  |
|------------------------------|------------------------------|----------------------------------------|
| `node_id`                    | `GraphNode.id`               |                                        |
| `node_type`                  | `GraphNode.kind`             | unmapped values → `'unknown'`          |
| `attributes.name`            | `GraphNode.label`            | fallback: `node_id`                    |
| `attributes.file`            | `GraphNode.file`             | fallback: `''`                         |
| `attributes.content`         | `GraphNode.content`          | fallback: `''`                         |
| `attributes.record_id`       | `GraphNode.recordId`         | fallback: `''`                         |
| remaining `attributes.*`     | `GraphNode.metadata`         |                                        |
| `source_id`                  | `GraphEdge.source`           |                                        |
| `target_id`                  | `GraphEdge.target`           |                                        |
| `relation`                   | `GraphEdge.edgeType`         | unmapped values → `'related'`          |
| `attributes.confidence`      | `GraphEdge.confidence`       | fallback: `1.0`                        |
| remaining `attributes.*`     | `GraphEdge.metadata`         |                                        |

**Never invent semantic relations in the frontend.** If a relation does not appear in the `GraphEdgeType` union above it must not be given a special meaning in any renderer.

---

## Backend field names (important)

The Python backend uses different field names than the domain artifact. Relevant for reading `codecompass_graph_store.py` output or the REST API:

| Backend field  | Meaning               | domain artifact key   |
|----------------|-----------------------|-----------------------|
| `kind`         | node category         | `node_type`           |
| `edge_type`    | relation category     | `relation`            |
| `confidence`   | probability score     | `attributes.confidence` |
| `record_id`    | originating record    | `attributes.record_id` |

Do **not** use `type` for nodes or `weight` for edges — those fields do not exist in the backend model.

---

## REST API

All endpoints require `Authorization: Bearer <token>` and are registered under `codecompass_graph_bp`.

| Method | Path | Parameters | Response |
|--------|------|-----------|----------|
| GET | `/api/codecompass/graph` | `knowledge_index_id` | `domain_graph_artifact.v1` |
| GET | `/api/codecompass/graph/node/<node_id>` | `knowledge_index_id` | single-node artifact |
| GET | `/api/codecompass/graph/expand` | `knowledge_index_id`, `seed`, `profile` | subgraph artifact |

### Expansion profiles

| Profile name          | Max depth | Max nodes | Use case                         |
|-----------------------|-----------|-----------|----------------------------------|
| `bugfix_local`        | 2         | 20        | Tracing a single bug callsite    |
| `refactor_navigation` | 3         | 30        | Finding all affected callers     |
| `architecture_review` | 3         | 40        | High-level dependency mapping    |
| `config_integration`  | 2         | 24        | Config-to-bean wiring analysis   |

---

## Adding a new renderer

1. Create a standalone component in `components/<name>-view/`.
2. Declare `@Input() graph: GenericGraphModel | null = null` and `@Input() selectedNode: GraphNode | null = null`.
3. Declare `@Output() nodeSelected = new EventEmitter<GraphNode>()` and `@Output() edgeSelected = new EventEmitter<GraphEdge>()`.
4. Consume only `graph.nodes` and `graph.edges` — never import backend models.
5. Use a dynamic `import()` for any heavy library to keep it out of the main bundle.
6. Register the new `GraphViewMode` in `models/graph-view-mode.ts` and add a `@case` branch in `graph-viewer.component.ts`.
7. Add the new mode button to `graph-toolbar.component.ts`.
8. Write a spec that verifies `nodeSelected` and `edgeSelected` emit correctly without requiring a real canvas/WebGL context.

---

## Filter model

`GraphFilter` (defined in `models/graph-filter.model.ts`) controls what `GraphStateService.filteredNodes()` and `filteredEdges()` return:

```typescript
export interface GraphFilter {
  nodeKindFilter: GraphNodeKind[];   // empty = show all kinds
  edgeTypeFilter: GraphEdgeType[];   // empty = show all edge types
  searchText:     string;            // matches label or file (case-insensitive)
}
```

`filteredEdges` automatically excludes edges whose source or target node was removed by the node filter — renderers do not need to enforce this.

---

## Testing

Test runner: `npm run test:unit` (Vitest) from `frontend-angular/`.

Mock data fixture: `testing/mock-codecompass-graph.ts` — exports `MOCK_DOMAIN_GRAPH_ARTIFACT` with 20 nodes (all 5 kinds) and 30 edges (all 12 edge types represented).

Adapter and state service tests use `new GraphAdapterService().fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT)` to build a real graph object without Angular DI.

Components use `ChangeDetectionStrategy.OnPush`; input changes in tests must be applied via `fixture.componentRef.setInput('inputName', value)` rather than direct property assignment.

Cytoscape is loaded via dynamic import; JSDOM will log a canvas warning during 2D renderer tests — this is expected and does not indicate a test failure.
