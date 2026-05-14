# Goal Execution Contract Coverage (GEC-T000)

## Scope

This note classifies current implementation coverage before introducing additional changes.

## Already Present (Partial)

- Planning proposal contract validation exists (`agent/services/planning_proposal_service.py`).
- Expected artifacts exist in multiple proposal and worker contract structures.
- Artifact verification and completion services already exist.
- Tool intent resolver already performs conservative remapping.
- Task scoped execution already persists traces and research artifacts.

## Missing Before This Slice

- No single goal-level `GoalExecutionContract` schema and adapter.
- No guaranteed attachment of execution contract at goal creation.
- No guaranteed propagation of task-scoped execution contract during task materialization.
- No plan-level mandatory artifact expectation signal for software-project-like goals.
- No explicit prompt context bundle contract summary in tool-calling strategy prompt.

## Implemented In This Slice

- Added `GoalExecutionContractService` with:
  - default contract creation
  - compatibility adapter behavior
  - attachment helper for goal execution preferences
  - task-scoped contract derivation helper
- Goal creation route now injects `goal_execution_contract` into execution preferences.
- Task materialization now propagates `worker_execution_contract`.
- Planning proposal generation/validation now supports:
  - expected artifacts per node
  - software-project artifact defaults
  - repair hints for missing artifact expectations
  - artifact trace IDs
- Tool-calling strategy prompt now includes a compact prompt-context-bundle summary.

