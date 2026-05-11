# Deterministic Repair Runtime

## Why Unstructured Context Is Not Sufficient

When a repair task is passed to the Worker as prompt text only, execution is LLM-guided—not deterministic. The Worker may produce different repair steps on each run, cannot enforce bounded safety limits, and cannot guarantee verification or rollback. Deterministic repair requires machine-readable contracts at every boundary.

## Architecture Overview

```
Hub                             Worker
─────────────────────────────   ──────────────────────────────────
Signature Matching              RepairProcedureRunner
  └─ build_initial_failure_       └─ run_plan(ExecutionEnvelope)
     signature_catalog()              └─ _execute_step(step)
                                           └─ check_unsafe_guardrails()
Plan Generation                            └─ map_step_to_tool()
  └─ generate_repair_               └─ run_step(step_envelope)
     execution_plan()
                                RepairVerificationRunner
Catalog Lookup                    └─ verify_step()
  └─ repair_procedure_catalog       └─ verify_final()
     .lookup_catalog()
                                Before/After Evidence
Outcome Persistence               └─ _capture_before_evidence()
  └─ persist_repair_               └─ _capture_after_evidence()
     execution_result()
```

## Hub-Driven Step-by-Step Mode (DRR-T014)

The safest production model. Hub authorizes one step at a time:

1. Hub generates `RepairProcedureExecutionPlan` via `generate_repair_execution_plan()`
2. Hub creates `RepairStepExecutionEnvelope` for the current step
3. Worker runs `RepairProcedureRunner.run_step(step_envelope)`
4. Hub records the result and decides the next step

```python
from agent.services.repair_execution_orchestrator_service import run_hub_driven_repair

result = run_hub_driven_repair(
    procedure,
    task_id="task-001",
    approval_ref={"ref_id": "a-001", "operation": "admin_repair", ...},
)
```

## Worker-Run Full Procedure Mode (DRR-T015)

Hub sends the entire `RepairProcedure` inside `ExecutionEnvelope`. Worker runs all steps in bounded mode.

**Limits**: `max_steps=20`, `max_mutation_steps=5`, `max_runtime_seconds=300`.

This mode requires the `deterministic_repair_execution_enabled` feature flag to be `True`. Default: deployment-dependent.

## Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `deterministic_repair_analysis_enabled` | `True` | Signature matching and diagnosis |
| `deterministic_repair_preview_enabled` | `True` | Dry-run plan preview |
| `deterministic_repair_execution_enabled` | `True` (code) | Mutation execution (deployment sets False) |

## Safety Classes and Approval

| Safety Class | Approval Required | Capability Required |
|---|---|---|
| `inspect_only` | No | `repair.diagnose` |
| `bounded_low_risk` | No | `repair.execute.low_risk` |
| `confirm_required` | Yes | `repair.execute.low_risk` + `ApprovalRef` |
| `high_risk` | Yes | `repair.execute.approval_gated` + `ApprovalRef` |
| Unknown | Denied | — |

## Unsafe Action Guardrails

Blocked before any tool call:
- `rm -rf /`, `mkfs`, `dd if=`, `shutdown`, `reboot`, `dd of=/dev/`

Escalation required (out-of-scope):
- `terraform`, `kubectl`, `iptables`, `nftables`, `dsadd`

## Verification Requirements

- Mutation steps require `verification_after_step=True`
- Final verification runs before `RepairExecutionResult.status = success`
- Verification failure produces `verification_failed` or `partial_success` — never `success`

## Outcome Classification

Defined in `STANDARD_OUTCOME_LABELS`:
- `succeeded` — final verification passed
- `partially_helped` — some improvement, incomplete verification
- `failed` — execution failed or verification failed
- `regressed` — evidence shows worsening after repair

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/repair/analyze` | POST | Signature matching and evidence analysis |
| `/repair/preview` | POST | Generate repair plan without execution |
| `/repair/execute` | POST | Execute plan (admin-only) |
| `/repair/outcomes` | GET | Query repair outcome history |
| `/repair/diagnostics` | GET | Repair engine readiness state |
| `/repair/operator/view` | GET | Operator view for current repair session |

## E2E Test Commands

```bash
# Full deterministic flow
pytest tests/test_repair_runtime_e2e.py::TestHighConfidenceRepairFlowWithoutLLM

# Approval-required flow
pytest tests/test_repair_runtime_e2e.py::TestApprovalRequiredRepair

# Verification failure
pytest tests/test_repair_runtime_e2e.py::TestVerificationFailureAndNegativeLearning

# Failure modes
pytest tests/test_repair_runtime_failures.py

# Security regression suite
pytest tests/test_deterministic_repair_security.py

# Governance (safety, approval, guardrails)
pytest tests/test_deterministic_repair_governance.py
```

## Rollout Checklist

Before enabling `deterministic_repair_execution_enabled=True` in production:

- [ ] `tests/test_repair_runtime_e2e.py` — all E2E tests pass
- [ ] `tests/test_repair_runtime_e2e.py::TestApprovalRequiredRepair` — approval enforcement verified
- [ ] `tests/test_repair_runtime_e2e.py::TestVerificationFailureAndNegativeLearning` — failure path verified
- [ ] `tests/test_repair_runtime_failures.py` — malformed input tests pass
- [ ] `tests/test_deterministic_repair_security.py` — security suite passes
- [ ] `tests/test_deterministic_repair_governance.py` — governance tests pass
- [ ] Outcome persistence (`RepairExecutionRecordDB`) reachable in deployment
- [ ] Approval service configured for repair scope
- [ ] Operator notified that mutation execution is active
