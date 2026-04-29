# Ananta Native Worker

## Overview

The native worker executes Hub-delegated tasks and returns artifact-first outputs.  
The Hub remains the control plane for planning, policy, approval, and verification decisions.

## Modes

- `plan_only`
- `patch_propose`
- `patch_apply`
- `command_plan`
- `command_execute`
- `test_run`
- `verify`

## Execution profiles

- `safe`
- `balanced` (default)
- `fast`

Profile beeinflussen Budgets und Friktion, aber deaktivieren keine harten Invarianten.
Siehe `docs/worker/execution_profiles.md`.

## Safety boundaries

1. No unbounded repository execution context.
2. No patch apply without policy + approval binding.
3. No shell command execution without policy classification.
4. No hidden success on denied/degraded states.
5. Trace metadata is required for execution-capable paths.

## Golden path

1. Build command/patch plans from bounded context.
2. Propose artifacts.
3. Execute tests in constrained workspace.
4. Verify and summarize artifacts.
5. Apply only through approval-gated path.

## Known limitations

- External adapters are optional and may be unavailable.
- Autonomous loop is budgeted and intentionally conservative.
- Native flow assumes deterministic testable artifacts; it does not guarantee semantic correctness without verification evidence.

## Advanced flows

- Retrieval/indexing: `docs/worker/retrieval_and_indexing.md`
- Plan/state-machine flow: `docs/worker/plan_and_execution_flow.md`
- Standalone mode: `docs/worker/standalone_mode.md` and `docs/worker/standalone_quickstart.md`
