# KRITIS MutationGate Release Evidence Checklist

Use this checklist for release readiness and KRITIS evidence collection.

## 1. Configuration and deny switch

1. Verify `mutation_gate.enabled=true` in effective config/read-model.
2. Verify `mutation_gate.global_deny_mutations` state is explicit and documented for the release.
3. If global deny is enabled for a freeze window, confirm mutation-capable actions are blocked fail-closed.

## 2. Positive path (approved mutation)

1. Execute one approved mutation-capable flow (e.g., scoped write/apply).
2. Confirm operation succeeds only when policy + scope are valid.
3. Capture corresponding `mutation_gate_decision` audit with `outcome=allow`.

## 3. Negative paths (must block)

1. Execute mutation without approval scope or confirmation.
2. Execute mutation with expired scope.
3. Execute mutation with mismatched target/class binding.
4. Execute mutation with global deny switch enabled.
5. For each case, confirm blocked result and `mutation_gate_decision` audit with `outcome=blocked`.

## 4. Evolver-specific checks

1. Run evolver proposal apply path.
2. Confirm apply path crosses MutationGate boundary before provider apply.
3. Confirm blocked decision prevents apply execution.

## 5. Artifact + task-state checks

1. Trigger artifact mutation route (`upload`, `extract`, or `rag-index`) and confirm gate decision exists.
2. Trigger critical task-state mutation (`completed`, `failed`, `blocked`, `cancelled`) and confirm gate decision exists.

## 6. Evidence package

Store together:

- effective config snapshot (including mutation gate flags)
- audit extracts for allow + blocked MutationGate decisions
- short scenario notes (actor, trace/task/goal linkage, expected vs. observed result)
- release sign-off statement referencing this checklist

