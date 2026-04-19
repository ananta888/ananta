# Frontend UI Reuse Analysis

This inventory captures recurring Angular UI patterns that are good candidates for the shared UI kit.

## Priority Candidates

| Priority | Pattern | Current examples | Shared target |
| --- | --- | --- | --- |
| Critical | Empty states | Dashboard, Board, Artifacts, Templates, Goal Detail | `shared/ui/state/EmptyStateComponent` |
| High | Error states | Dashboard load errors, demo errors, API detail errors | `shared/ui/state/ErrorStateComponent` |
| High | Section cards | Dashboard panels, Goal summaries, Artifact groups | `shared/ui/layout/SectionCardComponent` |
| High | Metric cards | Dashboard stats, Goal result summary, Governance summary | `shared/ui/display/MetricCardComponent` |
| High | Wizard steps | Dashboard guided goal flow | `shared/ui/forms/WizardShellComponent` |
| Medium | Preset/action cards | Dashboard start actions, Help actions, Demo presets | `shared/ui/layout/ActionCardComponent` |
| Medium | Key-value grids | Task details, Goal governance, Artifact detail | `shared/ui/display/KeyValueGridComponent` |

## Keep Local For Now

- Terminal and xterm wrappers: implementation-specific and runtime-sensitive.
- Artifact flow explorer: still mixes orchestration data, workspace files and artifact selection.
- Team blueprint editors: domain-heavy forms with admin permissions and validation rules.
- Settings panels: good decomposition candidates, but many controls are configuration-domain specific.

## Extraction Guidance

Start with state primitives because they have the least domain coupling. Move layout and display primitives only after at least two concrete call sites share the same shape. Wizard extraction should follow after the current dashboard wizard stabilizes.
