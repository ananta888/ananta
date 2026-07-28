# Visual process embedding and runtime ownership

| State / action | Owner | Notes |
|---|---|---|
| Process definition (`VpGraph`) | visual-process persistence service | Versioned, editable definition; never receives runtime fields. |
| Selection, zoom and pan | `VisualProcessCanvasComponent` instance | Instance-local; no cross-editor globals. |
| Dirty and validation state | `VisualProcessEditorComponent` shell | Embedded and full editor instances remain independent. |
| Canvas rendering | `VisualProcessCanvasComponent` | The only node/edge SVG implementation; used by standalone, embedded edit and AI-Snake read-only modes. |
| Persistence, import/export, validation and dry-run | standalone/editor shell and API port | Hub endpoints remain authoritative. |
| Start, attach, refresh, detach, cancel and gate signals | `VpWorkflowRunnerService` / hub | Runtime is published as `VpRuntimeOverlay`; it does not mutate `VpGraph`. |
| Session/profile process resolution | chat process-binding service | Resolution order is global empty state → profile → session override. |
| Historical run graph | immutable run snapshot and SHA-256 | Later definition edits cannot alter a running or historical view. |

## Contracts and lifecycle

`VpRuntimeOverlay` identifies run, workflow, process ID/version and snapshot hash. It contains overall status, current steps, timestamps, errors, gate state and per-step states. Supported states are `pending`, `running`, `awaiting_approval`, `succeeded`, `failed`, `skipped`, and `cancelled`; unknown late steps are retained with `unknown` in Angular and safely normalized by the hub.

Every start stores a process-version snapshot and hash in the session run history. Definition saves archive the previous version as `<process-id>@<version>`. A missing requested version returns a typed missing-reference response instead of silently loading latest.

Gate decisions travel only through the hub. They require an idempotency key and record actor, workflow, step and decision. Browsers and workers never coordinate continuation directly.

## Embedding

Use `VisualProcessCanvasComponent` for rendering. Supply `graph`, optional `runtimeOverlay`, `selectedId`, `readOnly`, and one of `compact-readonly`, `embedded-edit`, or `full-editor`. Mutations are outputs. Use `VpStepInspectorComponent` with `mode="runtime-readonly"` for live inspection. The full editor remains the shell for persistence, validation, dry-run, import/export and editing.

AI-Snake resolves the active conversation automatically, lists its runs, keeps the selected historical snapshot, and stops polling on session/component teardown. Sessions without a reference render an explicit empty state.

## CaseFlow application projection

CaseFlow reuses the visual-process definition as its source of truth. A published
scenario stores the versioned manifest `ananta.caseflow.ui/v1` under the
`ananta.caseflow.ui` graph extension and adds the `caseflow` tag. There is no
second workflow store and no browser-side orchestration path.

The CaseFlow Studio embeds `VisualProcessEditorComponent` in `embedded-edit`
mode. Before generating or publishing a manifest it reloads the latest graph
revision, so the manifest is derived from the saved workflow rather than stale
editor state. Runtime views use `compact-readonly`; execution and gate decisions
continue to travel through the hub.

The manifest is deliberately declarative. It may select only these generic,
reviewed Angular blocks:

- `summary`
- `metrics`
- `case-list`
- `actions`
- `artifacts`
- `workflow`

Unknown block kinds make a manifest invalid. AI assistance therefore edits the
workflow and bounded metadata instead of generating arbitrary Angular classes or
template expressions. Specialized existing applications such as Bewerbungen
remain supported alongside generated views; shared routes are additive and
legacy `/classroom` links redirect to `/caseflow/classroom`.
