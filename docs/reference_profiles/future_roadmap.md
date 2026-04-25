# Reference Profiles Roadmap (Post-MVP)

This roadmap isolates candidates for later expansion. It is intentionally separate from the MVP starter rollout.

## MVP baseline (already shipped)

- `ref.java.keycloak`
- `ref.python.ananta_backend`
- `ref.angular.ananta_frontend`

## Candidate additions (later phases)

1. Spring Boot service profile for lightweight Java business APIs.
2. FastAPI profile for Python service ecosystems with async-first architecture.
3. React/Next.js profile for public-facing frontend architecture.
4. Data-pipeline profile for batch/stream processing conventions.

## Entry criteria for any new reference

- Curated source quality review completed (architecture signal, maintenance quality, boundary clarity).
- Strengths and limitations documented before activation.
- Governance boundaries and audit marker compatibility verified.
- At least one reproducible golden-path example defined.

## Out of scope for this MVP

- Large open-ended profile ingestion from arbitrary repositories.
- Automatic profile admission without curation.
- Any change that weakens policy/approval/security constraints.
