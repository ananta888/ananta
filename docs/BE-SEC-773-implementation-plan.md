# BE-SEC-773 implementation plan

This document captures the intended code changes for least-privilege access and team-scoped visibility in the goal workflow.

## Scope

- limit governance-summary responses for non-admin callers
- keep detailed policy decision and verification record payloads for admins only
- enforce team-scoped visibility for goal and plan reads when a user token is used

## Proposed enforcement rules

1. Agent token or admin user:
   - full goal visibility
   - full plan visibility
   - full governance summary

2. Non-admin user token:
   - goal access only when `goal.team_id` is empty or matches `g.user.team_id`
   - plan access only when the owning goal is visible
   - governance summary returns totals and status only

## Proposed helpers

- `can_access_goal(goal, require_admin_details=False)`
- `sanitize_governance_summary(summary, is_admin)`
- `team_scope_allows(goal, user_payload)`

## Notes

The current repository tooling in this session allows direct creation of new files and commits, but updating existing tracked files still requires an extra SHA-aware path that is not exposed by the current tool schema. This file is therefore committed as the concrete implementation guide for the next patch step.
