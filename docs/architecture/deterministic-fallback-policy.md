# Deterministic Fallback Policy (GEC-T031)

## Default principle

For complex software goals, default execution is LLM-first with guardrails.
Deterministic paths are not the primary path unless explicitly requested.

## Allowed deterministic usage

Deterministic execution is allowed only when at least one condition is true:

- explicit strategy mode requests deterministic behavior
- bounded fallback after LLM unavailability under policy
- safety-constrained recovery path requires deterministic control

## Required audit evidence

Whenever deterministic fallback is used, the system must persist:

- effective strategy mode
- selected strategy and fallback reason
- trace correlation (`trace_id`, `goal_id`, `task_id`)
- policy source and guardrail outcome

## Denied usage

Deterministic fallback should be denied when:

- contract requires LLM-first and no fallback exception is configured
- required artifact evidence would become unverifiable under deterministic shortcut
- security policy demands review before execution

