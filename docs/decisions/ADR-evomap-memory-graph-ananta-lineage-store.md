# ADR: Evomap Memory Graph / Ananta Lineage Store

## Status
Accepted (partial implementation)

## Context
The operations runbook documents canonical ownership for scoped runs, assets,
audit records, and provider references. Provider IDs remain unverified references.
Per-run provider workspaces are temporary. The lineage store must support validated
outcome/lineage mirrors, central retention/privacy deletion, and historical
cross-engine reconstruction.

## Decision
- **Hub owns** scoped runs, assets, and audit records.
- **Provider IDs** are unverified references; no trust is implied.
- **Per-run provider workspaces** are temporary and not persisted beyond the run.
- The lineage store exposes:
  - Validated outcome/lineage mirrors (pending validation pipeline).
  - Central retention/privacy deletion hooks (centralized, not per-provider).
  - Historical cross-engine reconstruction via immutable audit entries.

## Consequences
- Artifact and memory audit services are Hub-scoped; provider metadata is informational only.
- Temporary workspaces reduce long-term exposure of unverified provider data.
- Central retention/privacy deletion must be implemented at the Hub level to avoid
  fragmented deletion across engines.
- Historical reconstruction depends on immutable, append-only audit entries.

## Alternatives Considered
1. **Provider-owned lineage** — rejected: violates canonical Hub ownership and increases
   trust in unverified provider IDs.
2. **Permanent per-run workspaces** — rejected: conflicts with temporary workspace policy
   and retention/privacy goals.
3. **Distributed deletion** — rejected: central retention/privacy deletion is required
   for consistent cross-engine compliance.
