# CodeCompass Graph Viewer

The Angular CodeCompass graph viewer renders one canonical graph as a simple
list, a Cytoscape 2D graph, or a `3d-force-graph` scene. Visual profiles,
metric scoring, colors, legends, and filtering are renderer-independent.

Semantic Translation Graph extensions remain documented in
`docs/codecompass-semantic-translation-graph.md`. n8n indexing details remain
documented in `docs/codecompass-n8n-workflows.md`.

## Ownership and data flow

```text
Hub-owned indexing task
                │
                ▼
Worker-local CodeCompass output
  - materializes cc_graph_index.json
  - materializes graph_visual_metrics.v1 sidecar
  - computes supported metrics
  - records capability, provenance, limits, revision, and content hash
                │
                ▼
Existing worker artifact port
  - publishes immutable graph and sidecar references
  - no shared container filesystem
                │
                ▼
Hub artifact admission
  - verifies size, transport hash, schema, graph revision, and content hash
  - materializes both files into Hub-owned storage
  - persists one revision-bound graph-artifact binding on the index/run
                │
                ▼
Hub: CodeCompassGraphProjectionService
  - resolves the admitted Hub-local binding (legacy output_dir is fallback-only)
  - validates and reads the sidecar
  - projects additive fields into domain_graph_artifact.v1
  - never calculates centrality, descendants, or blast radius in HTTP routes
                │
                ▼
GraphAdapterService → GenericGraphModel
                │
        ┌───────┴────────┐
        ▼                ▼
viewer-local state       graph/profile projection cache
filters, selection,      colors, normalized scores,
hover, active profile    styles, breakdowns, legend counts
        └───────┬────────┘
                ▼
simple list | Cytoscape 2D | 3d-force-graph
```

The Hub remains the control plane and owns task delegation, artifact admission,
and the binding used by HTTP routes. A worker does not create tasks or
orchestrate another worker. Complex metrics are materialized only in a worker.
The Worker and Hub may run with completely separate filesystems; the only
handoff is the existing artifact port. The Hub projection service is
intentionally a read/validation boundary.

The implementation separates these responsibilities:

- `worker/retrieval/codecompass_graph_visual_metrics.py`: metric materialization
- `worker/retrieval/codecompass_graph_artifact_materializer.py`: worker-local graph/sidecar build
- `agent/services/knowledge_index_worker_artifact_service.py`: Hub admission and local materialization
- `agent/services/codecompass_graph_artifact_resolver.py`: admitted-binding resolution
- `agent/services/codecompass_graph_projection_service.py`: Hub-side projection
- `services/graph-adapter.service.ts`: API-to-Angular mapping
- `services/graph-visual-profile-validator.ts`: pure untrusted-profile validation
- `services/graph-visual-profile-storage.port.ts`: persistence abstraction
- `services/graph-metric-score.service.ts`: pure normalization and scoring
- `services/graph-color.service.ts`: canonical identities, colors, and markers
- `services/graph-visual-projection.service.ts`: revision/profile cache and styles
- renderer components: direct consumption of the projected styles

This split protects SRP and DIP. The older Self-Graph route still combines
several legacy responsibilities; new metric or visualization logic must not be
added to that route.

## Project-bound inventory and staged loading

The CodeHug viewer reads the active, authorized Source-Control-v1 index. It
does not fall back to the repository-global Self-Graph routes. Large graphs use
three additive read modes on
`GET /api/source-control/v1/connections/<connection_id>/graph`:

| View | Purpose |
|---|---|
| `inventory` | Lightweight, paginated domain/subdomain hierarchy and exact graph totals. |
| `topology` | Connected visualization window, optionally selected by an opaque domain key. |
| `staged` | Lossless, revision-bound pages; `stage=nodes` and `stage=edges` are paged separately so cross-page edges are retained. |

The existing `default` and `topology` calls remain compatible. Optional
`domain_scope` and `include_subdomains` filters are applied by the Hub before a
topology window is selected. Domain identity precedence is explicit
`domain_id`, then `domain_path`, then a repository-relative path fallback;
unassigned nodes remain a visible inventory facet. Scope keys are opaque and
must only be reused with the connection/index revision that issued them.

Domain and relation inventories are bounded and independently cursor-paged.
Inventory and staged cursors bind the graph revision, scope digest and stage.
An index change fails a continuation as stale instead of mixing revisions; a
scope or stage change fails closed as a cursor-scope mismatch. Delivery
completeness is separate from semantic materialization completeness. A fully
delivered graph may therefore still report a partial semantic budget.
Unresolved relations are counted explicitly and remain available in the staged
edge stream instead of being silently dropped from the result.

`content_graph_revision` binds paging and viewer state to the actual node/edge
content. The existing evidence revision remains separate so a valid Worker
visual-metrics sidecar keeps its original immutable evidence binding. Content
digests and global parallel-edge identities are computed once per bounded
cached snapshot, not once per continuation page.

The Angular viewer starts with 100 nodes and at most 400 edges. Users can pick
an explicit 250-node or 500-node strategy and expand the connected window in
bounded steps. The UI always reports full-index, selected-scope, loaded-window
and visible counts. Reaching the 500-node visualization budget does not imply
that the remaining records disappeared: their count stays visible, the domain
tree can narrow the server scope, and the staged API remains lossless.
Untrusted domain values are bounded to 4,096 characters, 255 characters per
segment, 64 hierarchy levels and a fixed expanded-prefix budget. Values beyond
those limits remain visible in the `unassigned` scope instead of allocating an
unbounded hierarchy or dropping their nodes.

The Hub caches a bounded set of already resolved graph stores by graph/metrics
file identity. Artifact authorization and digest verification remain in the
resolver; the cache only avoids reparsing an unchanged admitted snapshot. This
keeps the optimization behind a read seam and preserves least privilege.
The first read of a newly admitted legacy JSON snapshot must still parse that
bounded artifact once. Truly disk-lazy cold reads require a future indexed
SQLite or DuckDB read model; subsequent reads use the bounded cache.

## Artifact contracts

### `domain_graph_artifact.v1`

The existing graph contract remains backward-compatible. New fields are
optional, so legacy artifacts are still accepted. The authoritative schema is
`schemas/artifacts/domain_graph_artifact.v1.json`.

Relevant additive fields are:

- `metadata.graph_revision`: exact projected graph revision used by normalization and caches
- `metadata.evidence_graph_revision`: immutable Worker evidence revision
- `metadata.parent_graph_revision`: evidence revision of a projected subgraph, when applicable
- `metadata.projection_algorithm_version`: Hub projection contract version
- `metadata.visual_metrics_content_hash`: accepted Worker-sidecar content hash
- `metric_capabilities`: availability and provenance per metric
- node `attributes.raw_node_type` and `attributes.known_kind`
- edge `attributes.raw_edge_type` and `attributes.known_relation`
- node or edge `attributes.metrics`
- canonical `domain_id` and `domain_path`
- edge `multiplicity`, `directed`, and `self_loop`

For a full graph, projected and evidence revisions are normally identical. An
expansion or filtered Self-Graph gets its own deterministic projection revision
while retaining the evidence revision for sidecar validation. This prevents two
different subgraphs from sharing a style-cache key without weakening the
Worker-evidence binding.

`confidence: 0` is a valid explicit value; the valid range is `0..1`. It must
never be replaced by a truthiness fallback. Parallel edges remain separate
unless the source artifact explicitly supplies an aggregate `multiplicity`.

Unknown raw node and relation values are retained byte-for-byte in the Angular
model. A known-kind/relation field supplies optional registered semantics. An
unknown relation may receive a neutral visual fallback, but neither Hub nor UI
may invent a semantic relation.

### `graph_visual_metrics.v1`

The worker sidecar is revision-bound and content-hashed. Its schema is
`schemas/artifacts/graph_visual_metrics.v1.json`. Every capability has one of
these states:

| State | Meaning |
|---|---|
| `available` | The declared algorithm produced the value for its full scope. |
| `approximate` | A bounded or approximate algorithm produced the value. |
| `unavailable` | The required evidence or algorithm result is absent. |
| `not_applicable` | The metric does not apply to this graph/entity. |

Missing values are not converted to numeric zero. Capabilities include the
entity (`node`, `edge`, or `graph`), source, algorithm version, scope, optional
limits, evidence revision, and a stable reason code.
The Hub rejects stale revision hashes, invalid content hashes, non-finite
values, and malformed capability records, then returns a visible degraded
state instead of fabricated scores.

Supported canonical node metric IDs are:

- `in_degree`, `out_degree`, `total_degree`
- `direct_containment_children`, `descendant_count`
- `code_extent`, `usage_frequency`
- `degree_centrality`, `bridge_score`, `blast_radius`

Supported edge metric IDs are `confidence`, `multiplicity`, and
`dependency_weight`. Legacy `degree`, `code_size`, and `usage` payload names
are accepted only as adapter aliases to their canonical names.

## Angular graph model

`models/graph.model.ts` defines the canonical renderer input.

```typescript
interface GraphNode {
  id: string;
  kind: GraphNodeKind;          // registered semantic fallback
  rawNodeType?: string;         // original value, never discarded
  knownKind?: GraphNodeKind | null;
  label: string;
  file: string;
  content: string;
  recordId: string;
  domainId?: string;
  domainPath?: string;
  metrics?: GraphMetricVector;
  metadata: Record<string, unknown>;
}

interface GraphEdge {
  id: string;                   // collision-free for parallel edges
  source: string;
  target: string;
  edgeType: GraphEdgeType;      // registered semantic fallback
  rawEdgeType?: string;         // original value, never discarded
  knownRelation?: GraphEdgeType | null;
  confidence: number;
  multiplicity?: number;
  directed?: boolean;
  selfLoop?: boolean;
  metrics?: GraphMetricVector;
  metadata: Record<string, unknown>;
}
```

`GraphStateService` is provided by each `GraphViewerComponent`, not globally.
Two viewers therefore do not share graph data, filters, selection, hover, or
an unsaved active profile. The LocalStorage adapter and immutable projection
cache may be shared because their keys include graph context, revision, and
profile hash.

Filter selections use explicit `all`, `none`, or `subset` modes. The old
`__none__` TypeScript cast is not part of the contract.

## Visual profile

A `GraphVisualProfile` controls enabled metrics, weights, normalization,
direction, render ranges, highlight factors, color overrides, and legend
visibility. Five validated presets ship with the viewer:

- Struktur
- Abhängigkeiten
- Wichtigkeit
- Umfang
- Änderungsrisiko

The following is a complete minimal valid profile. The documentation test
extracts this block and validates it with the production validator.

<!-- graph-visual-profile-example:start -->
```json
{
  "schemaVersion": 1,
  "profileId": "example",
  "name": "Dokumentiertes Beispiel",
  "nodeMetrics": [
    {
      "metricId": "total_degree",
      "enabled": true,
      "weight": 1,
      "normalization": "log1p",
      "direction": "normal"
    },
    {
      "metricId": "direct_containment_children",
      "enabled": true,
      "weight": 0.5,
      "normalization": "sqrt",
      "direction": "normal"
    }
  ],
  "edgeMetrics": [
    {
      "metricId": "confidence",
      "enabled": true,
      "weight": 1,
      "normalization": "linear",
      "direction": "normal"
    }
  ],
  "nodeSizeRange": { "min": 5, "max": 24 },
  "edgeThicknessRange": { "min": 0.75, "max": 6 },
  "highlightFactors": { "hover": 1.2, "selected": 1.5, "connected": 1.1 },
  "domainColorOverrides": {},
  "nodeKindColorOverrides": {},
  "relationColorOverrides": {},
  "legend": {
    "showDomains": true,
    "showRelations": true,
    "showMetrics": true,
    "showUnavailable": true
  }
}
```
<!-- graph-visual-profile-example:end -->

### Import, export, and persistence

Profiles are treated as untrusted JSON:

- maximum UTF-8 size: 131,072 bytes
- file size is checked before reading browser file contents
- maximum nested object depth: 12
- only documented properties and metric IDs are accepted
- every number must be finite and within its documented bounds
- colors must be exactly `#RRGGBB`
- `__proto__`, `prototype`, and `constructor` are rejected
- CSS functions, URLs, unknown versions, and unknown fields are rejected
- an invalid import never changes active or persisted state

Exports are canonical JSON and contain no graph nodes, repository content,
tokens, or secrets. LocalStorage failures and quota errors are handled without
breaking the viewer. Reset removes the context-specific override and restores
the immutable default profile.

## Normalization and scoring

Normalization always uses the complete canonical graph for one revision, not
the currently visible subset. Filtering a domain or relation therefore does
not change the remaining nodes' base sizes or edge widths.

For every enabled, available metric `i`:

```text
x_i' = linear(x_i) | log1p(x_i) | sqrt(x_i)
n_i  = clamp((x_i' - min_i') / (max_i' - min_i'), 0, 1)
d_i  = n_i for normal direction, otherwise 1 - n_i
score = Σ(weight_i × d_i) / Σ(weight_i)
renderValue = renderMin + clamp(score, 0, 1) × (renderMax - renderMin)
```

For a constant metric range, `n_i` is deterministically `0.5`. Missing,
invalid, unavailable, or not-applicable metrics are removed from the active
denominator and remain visible in the score breakdown. If no active available
metric remains, the configured minimum render value is used with
`degraded_no_active_metric`.

Every breakdown records raw value, normalized value, normalization state,
weight, direction, partial score, availability, provenance, and reason code.
NaN, Infinity, and negative raw metrics never produce a non-finite render
value.

## Domains, colors, and legends

Canonical domain identity uses this precedence:

1. explicit `domain_id`
2. `domain_path`
3. documented file-path fallback
4. `unassigned`

Automatic colors and markers depend only on canonical ID and algorithm
version, not input order or current filters. A manual profile override wins.
Nodes without a domain receive a registered node-kind color or the neutral
unknown fallback. Color is never the sole encoding: every legend also displays
text and a marker.

The domain legend keeps every canonical domain, including hidden ones, and
shows total/visible nodes, internal edges, incoming/outgoing external edges,
and score sum. The relation legend is built from raw relation names and shows
total/visible edges, multiplicity sum, color, marker, and width breakdown.

Legend toggles and the toolbar update the same viewer-local filter state. Hover
is a temporary highlight layer and never changes filters or base scores.
Hidden entries remain in the inventory with a visible count of zero so they can
be enabled again without a backend request.

## Renderer capability matrix

| Capability | Simple | 2D | 3D |
|---|---:|---:|---:|
| Canonical color and marker | yes | yes | yes |
| Numeric score and availability | yes | yes | yes |
| Node area from projected size | no | yes | yes |
| Edge width from projected thickness | no | yes | yes |
| Full score tooltip | text detail | yes | yes |
| Direct style update without graph reload | yes | yes | yes |

2D and 3D consume identical base colors, scores, sizes, widths, and rankings.
They do not own palettes or normalization formulas. Highlighting multiplies an
entity's individual base value, preserving relative rank. A profile update
must not issue HTTP requests, replace graph data, destroy a renderer, or reset
viewport and selection.

All untrusted tooltip values are inserted as text. Do not introduce
`innerHTML`, sanitizer bypasses, or HTML-generating label callbacks.

## Cache and performance budgets

Projection keys contain projected graph revision, accepted visual-metric
content hash, projection algorithm version, and canonical profile hash. The
revision normalization context and projected styles use deterministic LRU
caches, each capped at eight entries. A source revision change removes its old
normalization and style entries. Highlight-only profile changes reuse the
semantic score/color projection; hover, selection, and visibility filters are
overlay operations and do not recompute revision-stable base scores.

Versioned budgets live in
`config/codecompass/graph_visualization_budgets.v1.json`. The release fixture
contains 5,000 nodes, 15,000 directed edges, 30 domains, known and unknown raw
types, partial metrics, explicit zero confidence, and a stable revision.

The release gate requires:

- 100 hover events: zero HTTP requests and zero base-score recomputations
- profile update: zero renderer reinitializations and graph-data resets
- at most one projection per animation frame
- deterministic eviction after more than eight revision/profile combinations
- all measured browser p95 values within non-zero versioned budgets
- a deterministic report without timestamps, absolute host paths, full source
  text, or secrets

Run the report validator with:

```bash
.venv/bin/python scripts/run_codecompass_graph_visualization_gate.py \
  --evidence artifacts/test-gates/codecompass-graph-visualization-evidence.json
```

The validator fails closed when evidence or budgets are missing, zero,
non-finite, stale, or over budget.

The browser runner writes its measurement handoff only after every Playwright
assertion passed:

```bash
cd frontend-angular
CCGV_MEASUREMENTS_OUTPUT=/tmp/ccgv-measurements.json \
  npx playwright test --config playwright.ccgv-graph.config.ts
```

The repository gate then combines that handoff with explicit, hash-bound
functional, security, accessibility, and production-build evidence. Every
referenced check file has a strict check ID/status/source-hash contract; a bare
boolean or an arbitrary file cannot attest a pass.

## Worker materialization and Hub admission

The optional `graph_visual_metrics` object is additive on asynchronous artifact,
collection, and source-record indexing requests. Its current contract is:

```json
{
  "schema": "codecompass_graph_visual_options.v1",
  "include_advanced_metrics": true,
  "blast_radius_seeds": ["provided-node-id"]
}
```

Seed IDs are caller-provided graph IDs; the Hub and Worker accept at most 256
unique configured IDs of at most 512 characters and never invent missing IDs.
Degree is always materialized. Bounded bridge metrics run only through 250
nodes; larger graphs expose the capability as unavailable with a reason code.
Blast radius is emitted for at most the first 25 valid configured seeds and
remains explicitly approximate and subset-scoped. Missing evidence produces a
valid degraded sidecar instead of a fabricated zero value.

The normal flow is:

1. The Hub persists one indexing intent in its task queue.
2. A Worker executes the delegated index and builds both graph artifacts in its
   own output directory.
3. The existing publisher uploads the immutable bytes and returns hash- and
   revision-bound references.
4. The Hub downloads and validates all referenced bytes before writing them to
   a run-specific Hub directory and updating index/run metadata.
5. Graph GET routes resolve the admitted binding. They never access the former
   Worker directory and never invoke degree, bridge, descendant, or blast
   algorithms.

Admission is disk-staged and memory-bounded. A single reference may declare at
most 128 MiB, all references of one index unit together at most 384 MiB, and
each graph JSON artifact at most 32 MiB. The default HTTP adapter streams and
hashes one-MiB chunks directly into a private Hub staging directory. Legacy
byte-returning adapters remain supported, but the Hub releases each payload
after staging it instead of retaining the complete unit in memory. Only a
fully verified unit is promoted; a budget, size, digest, schema, revision, or
content-hash failure removes staging and publishes no partial output.

Custom legacy publishers remain substitutable and may omit the new graph roles;
in that compatibility mode the resolver retains the existing `output_dir`
fallback. New default Worker executions require both graph artifacts together;
one without the other is rejected.

## REST API

All endpoints require `Authorization: Bearer <token>` and remain registered
under `codecompass_graph_bp`.

| Method | Path | Parameters | Response |
|---|---|---|---|
| GET | `/api/codecompass/graph` | `knowledge_index_id` | projected `domain_graph_artifact.v1` |
| GET | `/api/codecompass/graph/node/<node_id>` | `knowledge_index_id` | single node |
| GET | `/api/codecompass/graph/expand` | `knowledge_index_id`, `seed`, `profile` | projected subgraph |
| GET | `/api/codecompass/self-graph` | domain/detail/limit parameters | projected self graph |
| GET | `/api/source-control/v1/connections/<connection_id>/graph` | `view`, `limit`, `max_edges`, optional `cursor`, `stage`, `domain_scope`, `include_subdomains` | project-bound default page, inventory, topology window, or staged page |

Legacy clients may ignore every additive visual field. The normal graph,
expansion graph, and Self-Graph use the same projection contract.

## Testing

Targeted unit and contract tests:

```bash
cd frontend-angular
pnpm exec vitest run src/app/features/codecompass-graph

cd ..
.venv/bin/python -m pytest -q \
  tests/test_codecompass_graph_visual_metrics.py \
  tests/test_codecompass_graph_projection_service.py \
  tests/test_codecompass_graph_artifact_flow.py \
  tests/test_codecompass_graph_api.py \
  tests/test_codecompass_graph_visualization_gate.py \
  tests/security/test_codecompass_graph_visual_profile.py
```

The frontend gate covers profile validation, storage failure, 30-domain color
invariance, formulas, missing capabilities, raw types, parallel edges,
confidence zero, LRU behavior, two-viewer isolation, legends, settings,
renderer updates, XSS-safe tooltips, keyboard focus, and the large deterministic
fixture. Playwright adds real Simple/2D/3D, responsive drawer, network-count,
and Axe scenarios. The Angular production build is release-blocking.

## Adding a renderer

1. Create a standalone component below `components/`.
2. Accept canonical graph data and immutable node/edge style maps separately.
3. Emit selection intent; never import Hub or worker models.
4. Use existing profile, projection, filter, and legend state.
5. Load heavy libraries dynamically.
6. Update styles through the renderer API without replacing its instance.
7. Document supported properties in the capability matrix.
8. Add unit, direct-update, tooltip-security, and viewport-preservation tests.

## Troubleshooting

### Every metric is disabled

Inspect `metric_capabilities` and the visible reason codes. A missing or stale
worker sidecar is intentionally degraded; do not create zero-valued metrics in
the UI. Re-run the Hub-owned indexing/metric task for the graph revision.

### Sizes change after filtering

This indicates normalization was run over the filtered graph. Pass the full
canonical revision to `GraphVisualProjectionService` and apply visibility only
with the overlay state.

### A profile cannot be imported

Use the structured validator path and reason code displayed by the settings
drawer. Check schema version, byte size, unknown properties, finite numeric
bounds, and `#RRGGBB` color syntax. The previous profile remains active.

### A profile appears to affect another viewer

Confirm `GraphStateService` and `GraphVisualProfileFacade` are provided at the
`GraphViewerComponent` boundary. Only the storage adapter and projection cache
may be root-scoped.

### An unknown relation looks like `related`

The visual fallback may be `related`, but `rawEdgeType` must still contain the
original name and the legend must display it as semantically unknown. A missing
raw value is an adapter/projection defect.

## Architecture Query UI

The static architecture-query page is `web/www/codecompass/query.html`.
Operators configure the Hub URL and token, select a knowledge index, and run a
bounded query. Results show role, score, depth, classification, evidence paths,
warnings, and readable errors. Query results are not yet injected into the
Angular 2D/3D viewer; doing so requires the same `GraphAdapterService` boundary
and must not introduce a second graph contract.
