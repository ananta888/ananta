# OpenNotebook integration runbook

## Import

Import the offline fixture through the existing Sources API:

```bash
curl -X POST -H 'Content-Type: application/json' \
  --data @tests/fixtures/open_notebook/complex_export.json \
  http://localhost:5000/sources/import/open-notebook
```

The operator TUI equivalent is:

```text
:sources import-open-notebook tests/fixtures/open_notebook/complex_export.json
```

Open `system/sources` to inspect trust level, snapshots, citations, provenance
and Source Chat.

## Verification

Run:

```bash
pytest -q tests/test_open_notebook_e2e_import_retrieval.py \
  tests/test_open_notebook_e2e_source_chat.py \
  tests/test_open_notebook_backward_compatibility.py
cd frontend-angular && npm run test:unit -- --run \
  src/app/components/sources.component.spec.ts \
  src/app/features/sources/source-import-dialog.component.spec.ts \
  src/app/features/sources/source-chat-panel.component.spec.ts
```

Retrieval diagnostics are available in `RetrievalService` strategy output,
`RagService` explainability and `SourceChatContextService` budget/filter data.

## Troubleshooting

- `invalid_open_notebook_export`: validate the export against
  `schemas/integrations/open_notebook_export.v1.json`.
- `duplicate_content_hash`: the same external source version is already
  imported; changed content creates another snapshot under the same registry
  source.
- Empty retrieval: enable `RAG_SOURCE_OPEN_NOTEBOOK_ENABLED`, verify a
  completed OpenNotebook knowledge index and inspect source constraints.
- Secret redaction: inspect `redacted_fields`; secret values are never logged.
- Cloud scope denial: private imports default to `local_only`; explicitly
  approved metadata is required before an external provider can receive raw
  source text.

OpenNotebook remains an import/adapter target. Its database is not adopted as
Ananta's primary model; the hub continues to own policy, orchestration and
verification.
