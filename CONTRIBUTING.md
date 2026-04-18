# Contributing to Ananta

Thanks for your interest in contributing to Ananta.

## Project direction

Ananta is a local-first, hub-worker, multi-agent platform for secure AI-assisted development.
Contributions should respect the core architecture and existing project principles, especially:

- hub remains the central control plane
- workers execute delegated work only
- security, auditability, and clear responsibility boundaries are important
- incremental improvements are preferred over disruptive redesigns

Please read these files before contributing:

- `AGENTS.md`
- `LICENSE`
- `LICENSE.en.md`
- `LICENSE.de.md`

## Before opening a larger contribution

For bigger changes, please open an issue or start a discussion first.
This is especially important for:

- architectural changes
- changes to hub-worker boundaries
- licensing-related changes
- major security or execution model changes

## Code quality expectations

Please try to keep contributions:

- modular and understandable
- testable
- low-coupled
- security-aware
- compatible with existing architecture where possible

## Architecture Guardrails & PR Best Practices

To maintain code quality and architectural integrity, please follow these guardrails:

- **Thematic Focus:** Every Pull Request should address exactly one topic or issue. Avoid "kitchen sink" PRs that mix refactorings, bugfixes, and new features.
- **PR Size:** Keep PRs small and reviewable. Aim for less than 400 lines of code changes. Larger changes should be split into multiple incremental PRs.
- **Boundary Enforcement:** Do not bypass established layer boundaries. For example:
    - Routes should use Services, not Repositories directly.
    - Workers should not orchestrate other workers.
    - All work must flow through the Hub task system.
- **Dependency Discipline:** Be careful with adding new dependencies. Check if existing libraries can fulfill the need.
- **Automated Checks:** Ensure all local checks pass before pushing. We use a central check pipeline (`make check`).
- **Contract Stability:** Changes to central API schemas (Task, Goal, Provider) must be backward compatible or explicitly justified.

## Tests and checks

Run relevant checks before submitting changes, for example:

- backend tests with `pytest`
- backend linting with `python -m flake8 agent tests`
- security lint with `ruff check agent/ --select=E,F,W,S603,S607`
- frontend lint and tests where applicable

## Licensing of contributions

By submitting a contribution, you agree that your contribution may be included in this project under the repository license terms.

If this project continues with dual licensing, maintainers may ask contributors to confirm additional contributor terms in the future.
See also `CLA.md`.

## Pull request notes

Please keep pull requests focused and explain:

- what changed
- why it changed
- whether the change affects architecture, security, or compatibility

Thanks for contributing.
