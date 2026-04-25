# Reference Profiles Starter Pack (MVP)

This document defines the first curated reference profiles used by Ananta for guided **new-project** and **project-evolution** flows.

## Governance and usage boundary

- References are **guidance**, not clone templates.
- References may influence architecture and conventions, but never bypass policy, approval, or security controls.
- Every selected profile must remain visible in goal read models and audit markers.

## Starter profiles

| Profile ID | Fit scope | Strengths | Limitations |
| --- | --- | --- | --- |
| `ref.java.keycloak` | Java security-heavy backend services | mature authn/authz patterns, enterprise modularity, security-centric boundaries | not a universal Java template, too heavy for very small services |
| `ref.python.ananta_backend` | Python orchestration/governance backends | hub-worker boundaries, policy+approval integration, artifact-first execution surfaces | domain-specific shape, can be overkill for minimal CRUD APIs |
| `ref.angular.ananta_frontend` | Angular workflow/admin frontends | workflow-oriented UI structure, modular Angular layout, integration-friendly conventions | not a generic consumer-app template, admin focus may be too broad for simple UIs |

## Golden path examples

### 1) Java / Keycloak starter (new-project flow)

1. Create goal with `mode=new_software_project`, stack signal `Java + Keycloak`.
2. Confirm selected profile `ref.java.keycloak`.
3. Apply guidance to keep security/auth modules separated from feature modules.
4. Produce reviewable starter plan with identity-focused tests and policy checks.

### 2) Python / Ananta backend starter (new-project flow)

1. Create goal with `mode=new_software_project`, stack signal `Python API/Backend`.
2. Confirm selected profile `ref.python.ananta_backend`.
3. Use guidance for orchestration/service/persistence boundaries and trace visibility.
4. Produce starter backlog with governance-aware task sequencing.

### 3) Angular / Ananta frontend starter (project-evolution flow)

1. Create goal with `mode=project_evolution`, affected areas in frontend workflows.
2. Confirm selected profile `ref.angular.ananta_frontend`.
3. Use evolution hints for workflow state boundaries and UI/API adapter separation.
4. Keep changes in small reviewable UI increments with targeted tests.

## Reproducibility notes

- Use `/goals` with guided modes (`new_software_project`, `project_evolution`) and inspect returned `goal.reference_profile`.
- Inspect `/api/system/reference-profiles/catalog`, `/api/system/reference-profiles/retrieval-contract`, and `/api/system/reference-profiles/governance-contract`.
- Verify `reference_profile_used` audit entries for reference-aware goals.
