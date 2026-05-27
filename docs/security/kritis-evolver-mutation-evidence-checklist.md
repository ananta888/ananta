# KRITIS Evolver Mutation Evidence Checklist

Use this checklist for release and audit evidence of Evolver mutation enforcement.

## 1. Kill-switch and policy visibility

1. Verify `/evolution/providers` and `/evolution/providers/<name>` expose `mutation_gate.enabled` and `mutation_gate.global_deny_mutations`.
2. Verify effective config snapshot records mutation-gate state for the release.
3. If deny switch is active, confirm Evolver apply returns `mutation_gate_blocked:mutation_gate_global_deny`.

## 2. Review-first approval evidence

1. Create an Evolver proposal and inspect review context (`operation_class`, affected targets).
2. Approve via review endpoint and capture generated mutation-approval artifact ID.
3. Verify artifact includes proposal/task/trace binding, mutation class, target fingerprint, and expiry.

## 3. Pre-write boundary evidence

1. Execute apply with valid scoped approval artifact and capture successful gate decision (`allow`).
2. Execute apply with missing approval artifact and capture blocked result.
3. Execute apply with expired artifact and capture blocked result.
4. Execute apply with mismatched target fingerprint and capture blocked result.
5. Execute apply with wrong mutation class and capture blocked result.

## 4. Audit linkage

1. Collect `mutation_gate_decision` events for allow and blocked scenarios.
2. Ensure events contain proposal/task/goal/trace linkage.
3. Ensure events contain source marker and approval artifact ID (when present).

## 5. Evidence package

Store together:

- config + diagnostics snapshot (`mutation_gate` and evolution policy)
- review payload with structured review context
- approval artifact excerpt (redacted where needed)
- audit extracts for allow/blocked decisions
- final release sign-off statement referencing this checklist
