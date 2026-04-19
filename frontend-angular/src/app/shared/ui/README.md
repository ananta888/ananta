# Shared UI

`shared/ui` contains small, feature-free Angular UI primitives.

Use this area only when a component is reusable across at least two feature screens without importing domain concepts such as Goal, Task, Artifact, Worker or Team.

## Structure

- `state/`: empty, error, loading and notice primitives.
- `layout/`: section cards, page intros and repeated shell patterns.
- `display/`: metric cards, key-value grids, summary panels and table shells.
- `forms/`: wizard shells, mode pickers, preset pickers and form helpers.

Feature-specific panels stay in `components/` or `features/<area>/` until their repeated shape is clear.
