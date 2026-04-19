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

## Review Checklist

Use this quick check for every new Angular component or larger template change:

- Can an existing `shared/ui` primitive solve the repeated state, layout, display, form or action pattern?
- If a new primitive is proposed, are its inputs domain-neutral and free of services?
- Does the component use shared card, notice and status variants instead of ad hoc colors?
- Is there at least one focused unit or host render test for the reusable contract?
- Would the component still make sense outside its first feature screen?

## Design Tokens And Variants

Shared UI should use the semantic tokens in `frontend-angular/src/styles.css`:

- surface and text: `--bg`, `--fg`, `--muted`, `--card-bg`, `--input-bg`, `--border`
- semantic tones: `--tone-primary`, `--tone-info`, `--tone-success`, `--tone-warning`, `--tone-error`, `--tone-technical`
- layout: `--radius-card`, `--radius-control`, `--space-xs`, `--space-sm`, `--space-md`, `--space-lg`

Cards should prefer `SectionCardComponent` variants: `default`, `primary`, `success`, `warning`, `error`, `technical`.
Notices should prefer `ExplanationNoticeComponent` or `SafetyNoticeComponent` tones: `info`, `success`, `warning`, `error`, `technical`.

## Local Before Global

Not every extraction belongs in global shared UI. Keep a component local when it:

- names Goal, Task, Worker, Artifact, Team, Policy or Runtime in its public API
- depends on a feature service, route shape or API payload
- needs only one screen today
- would require feature-specific branches to be reusable

In those cases, extract a local feature component first and revisit global shared placement after the same shape appears in another screen.
