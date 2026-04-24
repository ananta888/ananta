# Eclipse Plugin Views Extension Foundation

This document captures the first meaningful implementation block of the Eclipse views extension track.

## Covered tasks (ECL-T27..ECL-T36)

- View strategy with MVP/phase-2/browser-only split
- Goal and quick action view contract
- Task list view contract
- Artifact view contract
- Context inspection view contract
- Basic task detail view contract
- Review and proposal view contract
- Blueprint/work-profile view contract
- Connection/runtime status view contract
- View navigation and linking model

## Design constraints

- Eclipse remains a thin work surface, not a second control plane.
- Complex admin/governance screens stay browser-first by policy.
- View links preserve object context and support explicit browser handoff.
- Context visibility and bounded payloads stay explicit for user trust.
