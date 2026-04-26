# Architecture drift report

This report records where implementation and architecture descriptions diverged and whether code or docs should move.

## Resolved high-priority drift

| Item | Decision | Why |
| --- | --- | --- |
| User command guidance drift (`python -m ...` vs `ananta ...`) | docs + output updated to user path | user-facing UX contract is `ananta ...`; module path remains dev fallback |
| Fallback vocabulary drift (`delegated|hub_fallback` vs runtime fields) | docs updated to runtime vocabulary with alias table | runtime payload fields are already test-backed and operational |
| Bootstrap OpenAI init flag drift (`--base-url`) | docs + installer outputs updated to `--endpoint-url` | parser contract in init wizard is explicit and executable |

## Medium-priority drift with explicit docs-first decision

| Item | Decision | Follow-up |
| --- | --- | --- |
| Python minimum version wording | docs aligned to runtime baseline (`3.10+`) | raise baseline only via separate code+CI migration task |
| CLI-first vs Docker-first onboarding narrative | docs split by user intent | maintain split in future docs updates and review |

## Current code-vs-description posture

- Core hub-worker orchestration claims are represented in code and tests and remain the canonical behavior source.
- Historical architecture documents are retained but lifecycle-labeled and linked to active/canonical sources.
- No runtime field rename is planned for fallback provenance unless clear interoperability/user value emerges.
