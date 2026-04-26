# Architecture source map

This map links architecture claims to executable modules/tests and marks whether each claim is currently enforced or aspirational.

| Architecture claim | Code anchors | Test anchors | Status |
| --- | --- | --- | --- |
| Hub owns orchestration and task queue | `agent/routes/tasks/autopilot.py`, `agent/routes/tasks/autopilot_tick_engine.py`, `agent/routes/teams.py` | `tests/test_tasks_autopilot.py`, `tests/test_task_orchestration_dependencies.py` | implemented |
| Worker executes delegated work only (no worker-to-worker orchestration) | delegation flow in `agent/routes/tasks/autopilot_tick_engine.py`, worker forwarding in `agent/routes/tasks/autopilot.py` | `tests/test_tasks_autopilot.py` | implemented |
| Hub fallback is controlled, observable, policy-bounded | fallback/provenance writes in `agent/routes/tasks/autopilot_tick_engine.py`, normalization in `agent/services/task_execution_tracking_service.py` | `tests/test_tasks_autopilot.py` | implemented |
| Goal -> Plan -> Task flow stays traceable | goal/task routes plus trace-aware read models (`agent/routes/goals.py`, task routes, execution tracking service) | `tests/test_goals.py`, `tests/test_task_execution_reconciliation.py` | implemented |
| Verification and artifact flow is explicit | `agent/routes/tasks/quality_gates.py`, artifact surfaces under `agent/routes/artifacts.py` | `tests/test_artifact_provenance_hashes.py`, e2e artifact/report tests | implemented |
| Release and safety gates are reproducible | `scripts/release_gate.py`, `scripts/run_release_gate.py` | `tests/test_run_release_gate.py`, `tests/test_release_gate_planning_cleanup.py` | implemented |

## Notes

- Historical roadmap artifacts remain for context but are not the canonical contract source.
- Canonical behavior references should prioritize runtime code plus tests over aspirational roadmap language.
