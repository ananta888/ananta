# Shared UI

`shared/ui` contains small, feature-free Angular UI primitives.

Use this area only when a component is reusable across at least two feature screens without importing domain concepts such as Goal, Task, Artifact, Worker or Team.

## Structure

- `state/`: empty, error, loading and notice primitives.
- `layout/`: section cards, page intros and repeated shell patterns.
- `display/`: metric cards, key-value grids, summary panels and table shells.
- `forms/`: wizard shells, mode pickers, preset pickers and form helpers.

Feature-specific panels stay in `components/` or `features/<area>/` until their repeated shape is clear.

## Variants

Use semantic variants instead of per-feature color classes:

- cards: `default`, `primary`, `success`, `warning`, `error`, `technical`
- notices: `info`, `success`, `warning`, `error`, `technical`
- status and metric tones: `success`, `warning`, `error`, `info`, `active`, `paused`, `unknown`

All shared styling should go through the design tokens in `src/styles.css`. Add a token before adding a one-off color to a shared component.

## Review Guardrails

Before adding or moving a component into `shared/ui`, check:

- no domain service dependency
- no public input names tied to Goal, Task, Worker, Artifact, Team, Policy or Runtime
- projected content is used for feature-specific actions or copy
- a focused test covers the reusable contract

If the shape is useful but still feature-specific, keep it near the feature first. Shared UI is for stable primitives, not premature generalization.
