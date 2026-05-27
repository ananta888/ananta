# KRITIS RAG Source Segregation Rules

## Purpose

Define explicit, testable source-mixing boundaries for retrieval chunks before prompt bundle assembly.

## Source classes

- `repo_code`
- `task_memory`
- `internal_docs` (artifact/index-derived internal documentation)
- `offline_wiki`
- `external_research`

## Segregation policy

For `local_only`, source mixing is permitted.

For cloud scopes (`trusted_private_cloud`, `external_cloud_allowed`) in `standard|strict` policy mode:

1. Determine anchor class from the top-ranked retained chunk.
2. Allow only classes from the anchor's allowed set.
3. Deny incompatible classes with explicit reason code `source_segregation_blocked:<class>_with_<anchor>`.

Allowed-set matrix:

- `repo_code` -> `repo_code`, `offline_wiki`
- `offline_wiki` -> `offline_wiki`, `repo_code`
- `internal_docs` -> `internal_docs`
- `task_memory` -> `task_memory`
- `external_research` -> `external_research`

## Rationale

- Prevent silent cross-domain blending in cloud-facing contexts.
- Keep mixing rules deterministic and auditable.
- Preserve a safe default posture while allowing local workflows to remain flexible.
