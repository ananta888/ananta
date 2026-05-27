# KRITIS RAG Filter Diagnostics

## Goal

Provide safe diagnostics for retrieval filtering decisions without leaking blocked content.

## Diagnostic outputs

`policy_filter` now includes:

- `input_count`, `allowed_count`, `denied_count`, `downgraded_count`
- `denied_by_reason`, `downgraded_by_reason`
- source-class contributions before/after filtering
- segregation metadata (`applied`, `anchor_source_class`, allowed classes)
- bounded decision list (source, engine, decision, reason, source class, sensitivity/classification)

## Safety constraints

- No raw blocked content is emitted in diagnostics.
- Decision rows contain only source identifiers and policy attributes.
- Downgraded chunks replace raw content with a fixed policy marker.

## Unknown sensitivity fail-closed behavior

For cloud scopes, missing/unknown sensitivity is denied with reason:

- `unknown_sensitivity_default_deny`

This makes fallback behavior explicit and observable for operators.
