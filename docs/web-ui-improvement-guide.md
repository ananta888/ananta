# Web UI Improvement Guide

## Goals
- Faster operator workflows
- Clear status and error feedback
- Consistent interaction patterns across all pages

## Implemented Baseline
- Global assistant dock remains available in the lower-right area.
- Assistant dock is split into orchestrator, message-list, controls, and storage concerns instead of one monolithic component.
- Added grouped top navigation context (`Operate` / `Configure`).
- Added a dashboard aggregated read model endpoint (`/dashboard/read-model`).
- Added client-side short TTL cache for read-model calls in frontend.
- Added onboarding checklist card for first-use setup progress.
- Added core admin E2E journey smoke coverage.
- Added repository `.editorconfig` enforcing UTF-8.

## Next Iterations
1. Replace component inline styles with reusable UI primitives.
2. Add direct Settings UI support for `local_openai_backends` and runtime profiles.
3. Introduce strict typed DTOs for all API responses.
4. Add unified async state component (loading/error/empty) on every major page.
5. Expand accessibility checks to all admin forms and keyboard-only flows.
