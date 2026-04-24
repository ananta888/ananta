# Deterministic Repair Path Operator Guide

This guide explains how to operate the deterministic-first repair path in Ananta.

## 1. Core flow

1. **Diagnosis**: structured evidence is collected and matched against known signatures.
2. **Proposal preview**: bounded repair steps are shown before mutation-capable actions.
3. **Execution**: bounded step execution runs with approval and safety checks.
4. **Verification**: per-step and final verification classify the outcome.
5. **Result recording**: outcome is written to repair memory and used for recommendations.

## 2. Deterministic vs escalated path

- **Deterministic**: known signature + sufficient confidence + no contradictory evidence.
- **Mixed**: ambiguous evidence requires additional deterministic branching and review.
- **LLM escalated**: unknown/low-confidence/contradictory states after bounded deterministic paths.

Escalation never bypasses approval. LLM output is converted into a reviewed, structured candidate procedure.

## 3. Approval and guardrails

- Action safety classes: `inspect_only`, `bounded_low_risk`, `confirm_required`, `high_risk`.
- Mutation-capable actions require scoped approval (procedure + target + session).
- Unsafe/out-of-scope actions are blocked by fail-closed guardrails.

## 4. Golden path examples

The default examples are:

1. `service_start_failure`
2. `package_install_failure`
3. `port_conflict`

Each example follows diagnosis -> proposal preview -> verification -> result recording.

## 5. Safety notes

- Command success alone is not treated as repair success.
- Contradictory or worsening signals trigger clean stop behavior.
- Negative learning can block repeated regressive procedures even when ranking score is high.

## 6. Rollout plan (phased)

1. **Pilot**: service start + package install classes with strict approval gates.
2. **Expanded common classes**: add port/path/compose classes under bounded guardrails.
3. **Governed default**: curated classes as default path with full audit traceability.
