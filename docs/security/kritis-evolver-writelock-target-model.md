# KRITIS Evolver Write-Lock Target Model

## Scope

This model aligns evolver write/apply behavior with the central MutationGate boundary.

## Mutation-capable evolver operations

The following evolver actions are treated as mutation-capable and must cross MutationGate:

- proposal apply that can change code or project artifacts
- patch/application style target references
- mutation-like artifact application steps

Analyze-only and validate-only evolver operations are explicitly non-mutation and remain outside write-lock enforcement.

## Mapping to MutationGate classes

Evolver apply path maps to MutationGate class `patch_apply` (or stricter class if policy resolves it).

Target context includes:

- `task_id`
- `goal_id`
- proposal target refs
- normalized mutation target fingerprint

## Boundary enforcement rule

Before provider `apply(...)` executes:

1. approval policy is evaluated
2. execution risk policy is evaluated
3. scoped mutation approval artifact (review-issued) is validated against task/trace/proposal/target fingerprint and expiry
4. MutationGate decision is evaluated and audited
5. blocked/confirm-required decisions fail closed

No provider apply call is allowed when MutationGate does not return `allow`.

## Scoped approval artifact model

Review approval creates a bounded mutation-approval artifact with:

- proposal/task/trace binding
- mutation class (`patch_apply`)
- normalized target fingerprint (derived from normalized `target_refs`)
- issuer and expiry window

Apply rejects missing, expired, or mismatched artifacts before provider execution.

## Review-first API surface

Evolution analyze/review/read-model responses include structured review context:

- operation class
- affected targets/files
- normalized target summary

This enforces review-first approval and avoids blind mass approval flows.

## Audit linkage

Evolver apply emits `mutation_gate_decision` records with source marker `evolution_service.apply` and proposal/task linkage for forensics and release evidence.
