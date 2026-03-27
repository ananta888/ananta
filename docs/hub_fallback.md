# Hub-as-worker fallback policy and operator controls

Purpose

Define when the hub may execute work locally (as a worker) and how that decision is recorded and controlled.

Policy

- Delegate-first: the hub must attempt to delegate to suitable remote workers whenever capability and policy checks pass.
- Fallback-only: the hub may execute locally only when no suitable remote worker is available and the hub is explicitly configured to allow fallback.
- Opt-in and guardrails: operators may configure hub self-execution modes: disabled, fallback-only, or always-eligible-for-specific-task-kinds.

Provenance and traces

When the hub executes a task locally, record execution provenance in traces and audit logs:

- executed_by: "hub-local" or "worker-<id>"
- delegation_decision: { "chosen": "hub-local" | "worker-<id>", "reason": "no_workers_available" | "policy_override" }
- capability_checks: list of capability validations performed before execution

Sample configuration (operator-level)

{
  "hub": {
    "self_execution_mode": "fallback", // "disabled" | "fallback" | "always"
    "allowed_task_kinds": ["analysis", "lint"]
  }
}

See docs/artifacts_and_routing.md for how provenance is surfaced in artifact and trace records.
