# KRITIS RAG Security Metadata Model

## Scope

This model defines mandatory security metadata for retrieval chunks and normalized retrieval documents.

## Canonical metadata fields

Each chunk carries `security_metadata` and mirrored top-level fields:

- `classification`: `public | internal | restricted | confidential`
- `source_origin`: `repo | artifact | wiki | external_research | task_memory`
- `sensitivity`: `public | internal_low | internal_medium | internal_high | confidential | secret | credential | customer_data | legal | security_sensitive`
- `tenancy`: tenant scope marker (default `single_tenant`)
- `approval_class`: approval/risk class marker (default `standard`)
- `chunk_security_tags`: chunk-level tags for fine-grained filtering/segregation

## Defaulting behavior

Defaults are source-aware and fail-closed enough for policy evaluation:

- unknown source origin falls back to normalized `source_type`
- missing sensitivity defaults by source:
  - `wiki -> public`
  - `repo -> internal_low`
  - `artifact -> internal_medium`
  - `task_memory -> internal_medium`
- classification is derived from sensitivity when not explicitly set
- tenancy defaults to `single_tenant`
- approval class defaults to `standard`

## Chunk-level tagging (K2-RAG-T02)

`chunk_security_tags` is attached per chunk and can differ from file-level assumptions.

Examples:

- `tenant:alpha`, `scope:payments`, `restricted`
- `domain:security`, `approval:operator_review`

If explicit chunk tags are missing, fallback tags are derived from classification/sensitivity/source-origin.

## Integration points

The normalized security model is injected during retrieval metadata normalization and therefore applies across:

- repo retrieval
- artifact/index retrieval
- wiki retrieval
- task memory retrieval

This provides one reusable metadata shape for future retrieval policy filters and segregation rules.
