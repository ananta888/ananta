# Tasks Review

This file lists tasks that are missing or incomplete per role based on `tasks_history` records.

## Architect
- Missing deployment diagram for production infrastructure under `architektur/uml/` and corresponding reference in `architektur/README.md`.
- **Additional suggestion:** add a class diagram to capture core backend entities.

## Backend Developer
- Documentation lacks database models and ORM usage in `src/README.md`.
- API authentication overview is absent from backend documentation.
- **Additional suggestion:** provide example authentication middleware and usage notes.

## DevOps Engineer
- Playwright tests are not integrated into a CI pipeline with caching.
- No caching strategy for Docker images in CI configuration.
- **Additional suggestion:** set up a GitHub Actions workflow that caches npm and pip dependencies.

## Frontend Developer
- Dashboard documentation missing environment setup details and API request/response examples.
- No component screenshots or state management notes in `frontend/README.md`.
- Theme switching and accessibility guidelines are not documented.
- **Additional suggestion:** include steps for running accessibility audits with tools like Lighthouse.

## Fullstack Reviewer
- Documentation has not been standardized or deduplicated in the root `README.md`.
- Terminology across backend and frontend docs has not been audited for consistency.
- Codebase lacks a review for security headers.
- **Additional suggestion:** introduce automated linting to enforce style guidelines.

## Product Owner
- Stakeholder feedback has not been incorporated to refine roadmap milestones.
- Beta user feedback plan is missing.
- **Additional suggestion:** create a survey template for beta testers and track responses.

## QA/Test Engineer
- E2E coverage for user authentication flow is missing.
- **Additional suggestion:** add cross-browser tests and stress testing scenarios.

