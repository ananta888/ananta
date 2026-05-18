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
- `docs/planning-pipeline.md`
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
- **Core-First Surface:** Prioritize Web UI, CLI, and API/Webhook quality before adding new external channel adapters.
- **Extension Discipline:** Third-party extensions must remain capability-bound and must not bypass governance, policy, or audit controls.
- **Ecosystem Maturity Gate:** Marketplace/ecosystem proposals require stable hardened core contracts first.
- **Frontend Reuse Check:** For Angular UI changes, check whether an existing `frontend-angular/src/app/shared/ui/` primitive already covers the pattern before adding new markup or a new component.
- **No Fake Generics:** Do not move a component into global shared UI if its API or behavior depends on Goal, Task, Worker, Artifact, Team, Policy, Runtime or another feature concept. Extract locally first.
- **Semantic Variants:** Prefer shared card, notice, status and metric variants over ad hoc colors or one-off visual classes.

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

## Commit messages

Format: `<type>(<scope>): <subject>`

**Allowed types:**

| Type | When |
|------|------|
| `feat` | new capability or endpoint |
| `fix` | bug fix |
| `security` | security hardening, redaction, auth |
| `test` | tests only, no production code change |
| `docs` | documentation only |
| `refactor` | restructure without behavior change |
| `chore` | deps, config, tooling |
| `perf` | performance improvement |
| `ci` | CI/CD changes |

**Allowed scopes (non-exhaustive):** `goal-config`, `autopilot`, `worker`, `terminal`, `llm`, `profiles`, `runner`, `audit`, `ssh`, `db`, `deps`, `ci`, `ctx`, `modelfile`

**Subject:** imperative mood, max 72 chars, no trailing period.

**Body (optional):** explain WHY, not WHAT. Code already shows what changed.

Good examples:

```
feat(goal-config): export ALLOWED_GOAL_CONFIG_KEYS as public frozenset
fix(goal-config): compute checksum over redacted snapshot not raw
security(goal-config): extend secret redaction markers with authorization and bearer
fix(modelfile): guard ollama-autoimport against self-referential alias
chore(ctx): increase OLLAMA_NUM_CTX default from 4096 to 32768
```

Bad examples — do not use:

```
fixup planning          ← no type, no scope, no information
update code             ← too generic
wip                     ← never merge to main
fix stuff               ← no scope, no subject
```

## Squash policy

Local WIP and `fixup` commits are fine during a work session. Before pushing to `main`, squash them into a single meaningful commit per logical change:

```
# before
5cbd64a0 fixup planning
3fe6add6 fixup planning
df73a811 fixup planning

# after squash
a1b2c3d4 feat(goal-config): add ALLOWED_GOAL_CONFIG_KEYS and override validation
```

Use `git rebase -i` to squash. If a session produced multiple logically separate changes, keep them as separate commits with distinct scopes.

No commit with `fixup`, `wip`, or `planning` in the subject may land on `main`.

## Pull request notes

Please keep pull requests focused and explain:

- what changed
- why it changed
- whether the change affects architecture, security, or compatibility

Thanks for contributing.
