# Web UI Improvement Guide

## Goals
- Faster operator workflows
- Clear status and error feedback
- Consistent interaction patterns across all pages

## Implemented Baseline
- Global assistant dock remains available in the lower-right area.
- Added grouped top navigation context (`Operate` / `Configure`).
- Added a dashboard aggregated read model endpoint (`/dashboard/read-model`).
- Added client-side short TTL cache for read-model calls in frontend.
- Added onboarding checklist card for first-use setup progress.
- Added core admin E2E journey smoke coverage.
- Added repository `.editorconfig` enforcing UTF-8.

## Next Iterations
1. Replace component inline styles with reusable UI primitives.
2. Introduce strict typed DTOs for all API responses.
3. Add unified async state component (loading/error/empty) on every major page.
4. Expand accessibility checks to all admin forms and keyboard-only flows.
5. Add richer assistant action cards for common operations (teams/templates/settings).
