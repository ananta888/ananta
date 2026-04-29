# Agent Operations Runbook

## Objective
Operate hub and workers for autonomous software iteration safely and continuously.

## Daily Operations
- Check central queue health in Operations Console.
- Verify active leases and stalled tasks.
- Review failed gates and escalations.
- Track profile KPIs (`safe|balanced|fast`) for success, block and degrade rates.

## Incident Handling
- Queue stall: release expired leases, requeue blocked tasks.
- Repeated task failures: lower concurrency and force manual review.
- Hybrid context degradation: switch to degraded mode and rebuild index.

## Recovery
- Rebuild semantic index when manifest drift is detected.
- Reset stuck worker circuits and replay pending tasks.
- Roll back failed code changes using repository workflow.

## Worker profile guidance

- `safe`: strict governance for sensitive runs.
- `balanced`: default for production-like autonomous development.
- `fast`: bounded higher-throughput mode for low-risk sandboxes.

Sandbox does not remove policy invariants. Runtime remains non-interactive: approvals are machine decisions, not user prompts.
