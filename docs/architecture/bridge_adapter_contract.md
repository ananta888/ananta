# Bridge Adapter Contract

## Purpose

This contract defines how thin domain client bridges (for example local Blender add-on bridges) can interact with Ananta without becoming backend plugins.

The Hub remains the control plane for orchestration, policy, approval and audit.

## Required operations

- `health`
- `capabilities`
- `capture_context`
- `execute_action`
- `report_result`
- `cancel` (optional where not practical)

Each operation is declared with support state, timeout and optional request/response schema references.

## Mandatory governance constraints

- Hub policy enforcement is mandatory.
- Bridge adapters cannot approve their own actions.
- Approval reference/token is mandatory for approval-required actions.

## Execution envelope requirements

Every bridge request/response envelope requires:

- `correlation_id`
- `domain_id`
- `capability_id`
- `action_id`
- approval token/reference
- result artifact metadata

This ensures bridge-level execution remains auditable and cannot bypass Hub policy/approval flow.

## Communication and safety

Allowed communication modes are explicitly declared (`http`, `websocket`, `grpc`, `ipc`, `stdio`, `inprocess`).

Adapter resolution is allow-list based. Domain descriptors only select an adapter type; they never trigger dynamic imports of executable backend code.
