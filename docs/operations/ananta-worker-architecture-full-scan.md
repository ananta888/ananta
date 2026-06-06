# Ananta Worker Architecture Full Scan

## Purpose

Architecture full scan is for requests that need coverage beyond top-N RAG snippets, for example:

- "Erstelle ein Mermaid Architekturdiagramm zum implementierten CodeCompass Worker Handoff."
- "Erklaere die Gesamtarchitektur mit Quellen."
- "Create an architecture diagram for all relevant worker handoff components."

Short questions such as "Was ist CodeCompass?" stay on the standard fast RAG path.

## Activation

The path is activated by a retrieval profile with:

```json
{
  "analysis_mode": "architecture_full_scan",
  "output_intent": "mermaid_component_diagram",
  "coverage_policy": "relation_expanded",
  "summary_policy": "rolling_structured"
}
```

`/snake/ask` with `debug=true` exposes `trace.rag.retrieval_profile.analysis_mode` so operators can verify whether a request was classified as a full scan.

## Workspace Artifacts

Full scan writes deterministic artifacts under `rag_helper/` in the worker workspace:

- `architecture-plan.json`: stable plan with `plan_id`, batches, planned refs, excluded refs, budget, and coverage.
- `architecture-progress.json`: resume state, processed batches, processed refs, omitted refs, summary hash, and artifact paths.
- `architecture-summary.json`: structured rolling summary with components, edges, entrypoints, data flows, security boundaries, config points, runtime paths, open questions, source evidence, and coverage.
- `architecture-diagrams.md`: final Mermaid/Markdown synthesis.
- `progress.md`: human-readable mirror for compatibility.

## Research Context

The old shape remains valid:

```json
{
  "repo_scope_refs": [{"path": "agent/services/rag_service.py"}]
}
```

For full scan, `architecture_scope.refs` can be provided and is preferred:

```json
{
  "analysis_mode": "architecture_full_scan",
  "retrieval_profile": {"analysis_mode": "architecture_full_scan"},
  "architecture_scope": {
    "refs": [
      {
        "path": "agent/routes/snakes.py",
        "role": "entrypoint",
        "component_hint": "AI-Snake API",
        "dependency_kind": "calls"
      }
    ]
  },
  "relation_edges": [
    {
      "from": "agent/routes/snakes.py",
      "to": "agent/services/rag_service.py",
      "relation_type": "calls",
      "confidence": 0.8
    }
  ]
}
```

## Settings

Safe defaults are local and bounded:

- `ANANTA_WORKER_FULL_SCAN_ENABLED=true`
- `ANANTA_WORKER_FULL_SCAN_MAX_BATCHES=8`
- `ANANTA_WORKER_FULL_SCAN_FILES_PER_BATCH=3`
- `ANANTA_WORKER_FULL_SCAN_MAX_REF_CHARS=4000`
- `ANANTA_WORKER_FULL_SCAN_SUMMARY_CHARS=12000`
- `ANANTA_WORKER_FULL_SCAN_MAX_TOTAL_REF_COUNT=120`

Existing `ANANTA_WORKER_CONTEXT_*` settings still control the normal fast path.

## Resume

If `architecture-progress.json` exists and its `plan_id` matches the current plan, processed batches are skipped. If the plan changes, a new `plan_id` is generated and old results are not mixed into the new run.

## Safety Rules

The final synthesis prompt requires source evidence for concrete components and edges. Inferred elements must be marked separately, and the model is instructed not to invent concrete file paths.

## Tests

Run:

```bash
pytest -q tests/test_ananta_worker_architecture_full_scan.py tests/test_snake_ask_full_scan.py
```
