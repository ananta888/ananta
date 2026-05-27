# KRITIS Workflow Determinism Implementation (K2-WFD-T03..T07)

## Implemented scope

The critical evolution apply path now uses an explicit workflow state model with reusable transition enforcement.

## Explicit state machine

- Service: `agent/services/critical_workflow_state_service.py`
- Workflow type: `evolution_proposal`
- Main states: `review_required` → `approved` → `apply_requested` → `apply_in_progress` → (`apply_prepared` or `applied`)
- Controlled fallback state: `blocked`
- Terminal states: `rejected`, `applied`, `failed`

Invalid transitions fail with structured code `workflow_invalid_transition`.

## Transition guard layer

The transition service enforces:

- allowed transition matrix per workflow type
- named guard checks (`workflow_guard_blocked`)
- auditable transition events via:
  - `execution_audit_event` (`workflow_transition`)
  - `critical_workflow_transition`

The same service supports additional workflow types (currently `repair_execution`) to keep guard logic reusable across critical flows.

## Explicit fallback behavior

Critical apply failures now produce explicit fallback state transitions instead of silent rerouting:

- fallback reason and cause are written to proposal metadata (`last_fallback`)
- state moves to `blocked` where policy/mutation constraints deny progress
- fallback remains visible in read-model output (`workflow` + `proposal_metadata`)

## Replayability and reconstruction

Each workflow transition is persisted in `workflow_state.history` and exposed in read-model payload:

- deterministic `state_path`
- `valid` replay verification
- transition count and terminal reachability

Operators can reconstruct critical flow progression without raw log parsing.

## Timeout and stuck-state handling

The state service provides timeout inspection and bounded recovery:

- stuck detection from `last_transition_at` + `timeout_seconds`
- timeout transition (`timeout`)
- bounded recovery attempt via explicit fallback (`blocked`) with `recovery_attempts` limit

This keeps timeout recovery paths explicit, bounded, and auditable.
