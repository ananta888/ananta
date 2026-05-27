# KRITIS Audit Integrity and Tamper Evidence

## Scope

This note defines the operational integrity model for `audit_logs` in Ananta and the minimum evidence operators can collect for KRITIS reviews.

## Implemented mechanism

Ananta writes audit entries with a chained hash model:

1. Every record stores `record_hash`.
2. Every new record stores the predecessor hash in `prev_hash`.
3. The hash payload binds `username`, `ip`, `action`, sanitized `details`, and `prev_hash`.

This creates a tamper-evident chain: changing or deleting historical content breaks downstream hash validation.

## Runtime integrity check

Operators can verify current integrity using:

- `GET /api/system/audit-logs/integrity`
- alias: `GET /audit-logs/integrity`

The endpoint reports:

- `tamper_evident_ok`
- `mismatched_prev_hash_ids`
- `invalid_record_hash_ids`
- `legacy_unhashed_ids` (pre-chain records kept for compatibility)

## Export and signing strategy

For long-term external evidence, use this two-step approach:

1. Export filtered audit data plus integrity report for a release window.
2. Sign the export bundle with organization-managed signing keys outside the runtime process.

This keeps runtime dependencies minimal while allowing formal attestation in regulated environments.

## Operational recommendation

- Treat non-empty `mismatched_prev_hash_ids` or `invalid_record_hash_ids` as a security incident.
- Allow `legacy_unhashed_ids` only for historical records predating chain introduction; shrink this set over time via retention/rotation.

