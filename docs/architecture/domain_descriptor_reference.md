# Domain Descriptor Reference

## Minimal descriptor template

```json
{
  "schema": "domain_descriptor.v1",
  "domain_id": "sample",
  "display_name": "Sample Domain",
  "version": "1.0.0",
  "lifecycle_status": "foundation_only",
  "runtime_status": "descriptor_only",
  "owner": "ananta",
  "description": "Descriptor-only baseline for a new domain.",
  "supported_clients": ["cli", "bridge"],
  "source_paths": {
    "descriptor_root": "domains/sample",
    "code_paths": ["client_surfaces/sample"],
    "docs_paths": ["docs/architecture/domain_integration_foundation.md"]
  },
  "capability_pack": "domains/sample/capabilities.json",
  "context_schemas": ["domains/sample/schemas/context.v1.json"],
  "artifact_schemas": ["domains/sample/schemas/artifact.v1.json"],
  "policy_packs": ["domains/sample/policies/policy.v1.json"],
  "rag_profiles": ["domains/sample/rag_sources/default.profile.json"],
  "bridge_adapter_type": "local_client_bridge_v1"
}
```

## Capability pack notes

- Every capability must include a unique `capability_id` and the same `domain_id` as the pack.
- Use `approval_required=true` for mutating/high-risk operations.
- Keep categories stable and descriptive (`scene`, `script`, `preview`, `export`).

## Policy pack notes

- Use `default_decision: "default_deny"` unless there is a strict reason not to.
- Add explicit allow rules for read-only operations.
- Route high-risk actions through `approval_required`.

## RAG source profile notes

- Profiles must include provenance (`owner`, `captured_at`, `explanation`).
- Use bounded `allowed_paths`, `include_globs` and `exclude_globs`.
- Keep `ingestion_path` on approved pipelines (`codecompass`, `rag_helper`, `codecompass/rag_helper`).

## Release-gate readiness rules

- Domains stay `planned` or `foundation_only` until runtime evidence exists.
- `runtime_mvp` / `runtime_complete` inventory entries require:
  - runtime files
  - smoke command definitions
  - runtime evidence references
- `scripts/audit_domain_integrations.py` enforces this without domain-specific hard-coding.

## Security warning

Descriptors are declarative contracts, not execution plugins.
Do not add dynamic code-loading behavior in descriptor processing paths.
