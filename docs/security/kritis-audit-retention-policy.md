# KRITIS Audit Retention and Storage Policy

This policy defines storage classes, retention windows, and archival handling for audit records.

## Storage classes

1. **Operational audit store** (`data/audit.log` + `AuditLogDB`)
   - Purpose: short-term operator diagnostics and incident triage.
   - Access: restricted to operators/admins under RBAC.
2. **Evidence export store** (release/incident evidence artifacts)
   - Purpose: long-term compliance and forensic reconstruction.
   - Access: restricted, immutable/export-focused workflows.

## Retention windows

1. **Operational short-term**
   - Keep 30 days of high-volume operational events.
   - Rotate daily or on size thresholds.
2. **Operational extended**
   - Keep 180 days for security-relevant events (`approval_event`, `write_operation`, `workflow_transition`, `llm_interaction`).
3. **Long-term evidence**
   - Keep 24 months for release-linked and incident-linked evidence exports.

## Rotation and archival

1. Rotate `audit.log` regularly (daily or by size).
2. Move rotated files to compressed archive storage.
3. Export signed/hash-linked evidence bundles for long-term retention.
4. Never archive unredacted sensitive payloads; retention applies to redacted audit records only.

## Deletion policy

1. Expired operational logs are purged automatically per retention class.
2. Evidence bundles are purged only after retention expiry and governance approval.
3. Purge actions themselves must be audited.
