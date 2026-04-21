# UI State Language Patterns

This document closes the standard UX language pattern for blocked, review-required, safe and result states.

## Reusable Components

- `app-decision-explanation`: explains why routing, review, policy or tool approval is visible.
- `app-next-steps`: turns blocked, failed, review or success states into actionable next steps.
- `app-safety-notice`: highlights safe, blocked, review-required or successful state changes.
- `user-facing-language.ts`: maps technical platform terms to user-facing explanations while preserving technical labels for expert contexts.

## Required Patterns

Blocked:
- Explain why work paused.
- Show the policy or safety boundary.
- Offer a next step such as opening Board, Settings or Goal details.

Review required:
- Explain that manual approval is intentional.
- Show approval/rejection controls only where the user can act.
- Offer next steps for governance mode, Board or Goal detail.

Safe boundary:
- Avoid framing safety as a failure.
- State that the hub is preserving control and auditability.
- Keep technical details available below the user-facing summary.

Success/result:
- Lead with user value: what is now ready or visible.
- Keep task IDs, trace IDs and raw technical references secondary or collapsible.
- Offer the next action: inspect goal, follow tasks or open artifacts.

## Applied Views

- Dashboard Quick Goal: expectation model, official UI path, value-first success, collapsible internal references.
- Dashboard Timeline: guardrail explanations and next steps.
- Goal Detail: result summary before trace details and next steps for open work.
- Task Detail: blocked/review safety notices, decision explanations and next steps.
- Auto-Planner: plan result and policy explanations.

## Completion Rule

The state is complete when standard views use shared explanation and next-step components, and technical labels remain available without being the first message shown to new users.
