# Frontend Shared UI Rules

This guide defines where reusable Angular UI building blocks belong and when a component should stay feature-specific.

## Placement Rules

- Put domain-neutral primitives in `frontend-angular/src/app/shared/ui/`.
- Put feature panels that mention Goal, Task, Worker, Artifact, Team, Policy or Runtime in their feature area or in `frontend-angular/src/app/components/`.
- Prefer `shared/ui/state`, `shared/ui/layout`, `shared/ui/display` and `shared/ui/forms` over a flat shared folder.
- Export shared primitives through the nearest `index.ts` barrel to make imports discoverable.

## Naming Rules

- Shared components use plain UI names: `EmptyStateComponent`, `ErrorStateComponent`, `MetricCardComponent`.
- Feature components keep domain names: `GoalDetailComponent`, `DashboardTimelinePanelComponent`, `ArtifactsComponent`.
- Avoid names that sound generic but encode domain behavior, such as `GoalEmptyStateComponent` in shared UI.

## Extraction Rule

A component may move to `shared/ui` when:

1. It has no domain service dependency.
2. Inputs are generic labels, descriptions, links, variants or projected content.
3. At least two screens can use it without branching on feature names.
4. Tests cover the generic contract, not one feature scenario.

When in doubt, keep the component local and document it as a candidate.
