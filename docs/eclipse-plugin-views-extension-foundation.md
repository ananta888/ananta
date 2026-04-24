# Eclipse Plugin Views Extension Foundation

This document captures the Eclipse views extension track as a **contract-first backend implementation**.

There is currently no shipped standalone Eclipse plugin UI binary in this repository.  
The implemented service contracts model and validate the view behavior that a thin Eclipse plugin must follow.

Runtime bootstrap is now in progress, but full Eclipse views runtime delivery is still pending; this document remains a foundation contract reference and not a complete runtime delivery claim.

## Covered tasks (ECL-T27..ECL-T50)

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
- Unified selection synchronization across linked views
- Minimal safe view-state persistence (without sensitive state persistence)
- Task filters/grouping and artifact rendering mode contracts
- Diff/review rendering hardening with clickable file references and explicit non-auto-apply guard
- Context source badges/provenance hints
- First-run perspective/layout recommendation
- Browser fallback policy for complex/admin surfaces
- View-specific error/empty-state catalog
- Accessibility and keyboard usage baseline contract
- Multi-view smoke checklist and view-coordination test matrix
- Later-phase evaluation contracts for knowledge/sources view and advanced admin views isolation

## Design constraints

- Eclipse remains a thin work surface, not a second control plane.
- Complex admin/governance screens stay browser-first by policy.
- View links preserve object context and support explicit browser handoff.
- Context visibility and bounded payloads stay explicit for user trust.
