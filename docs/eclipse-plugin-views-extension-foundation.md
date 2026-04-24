# Eclipse Plugin Views Extension Foundation

This document captures the Eclipse views extension track as a **contract-first backend reference with runtime follow-through**.

There is currently no shipped standalone Eclipse update-site bundle in this repository.  
The implemented service contracts model and validate the view behavior that a thin Eclipse plugin must follow, and the runtime registry/handlers now implement the core M9 command/view delivery path.

This document remains the foundation contract reference; runtime hardening and CI depth are now implemented in the runtime hardening block.

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
