# Audit Minimum (OSS)

## Purpose

Define the minimum append-oriented audit behavior required for OSS core security invariants.

This baseline is intentionally lightweight and does **not** require KRITIS/Enterprise infrastructure.

## Required OSS audit events

At minimum, append audit/trace events for:

- policy denial
- approval request
- approval decision
- execution result (success/failure)

Each event should carry stable references where available (task_id, goal_id, trace_id, plan_id, verification_record_id).

## Append-oriented behavior

- Audit entries are append-only records.
- New decisions/events must create new records, never silently overwrite prior decisions.
- Hash chaining or equivalent integrity hints are encouraged for tamper visibility in OSS.

## Data minimization

- Redact secrets/tokens/password-like values by default.
- Avoid storing full raw context payloads unless explicitly required.
- Prefer compact metadata (IDs, hashes, reason codes, bounded notes).

## OSS vs KRITIS/Enterprise boundary

Out of scope for OSS minimum:

- WORM/tamper-proof regulated storage
- signed audit ledgers and regulated evidence packages
- SIEM export requirements
- hardware attestation and formal compliance bundles

These can be added later as incremental hardening layers.
