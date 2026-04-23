# Unified Approval Model

## Goal

Define one approval model that all subsystems can use before execution of sensitive actions.

## Approval classes

1. `allow` (low risk): execute directly, trace only.
2. `confirm_required` (medium risk): explicit confirmation required.
3. `blocked` (high/critical risk): deny execution and escalate.

## Action coverage

This model applies consistently to:

- shell execution
- file mutation
- service/config mutation
- install/remove operations
- evolver apply operations
- future repair/admin mutation steps

## Policy factors

Classification uses:

- operation class
- task sensitivity/risk profile
- governance mode (`safe`, `balanced`, `strict`)
- actor permissions and scope
- environment constraints (local/dev/production posture)

## Governance compatibility

- **safe:** defaults toward `confirm_required`/`blocked` for mutation-heavy actions.
- **balanced:** mixed mode with explicit confirmation for medium risk.
- **strict:** deny-by-default unless operation is explicitly allowed and approved.

## Contract output

`ApprovalDecision` returns:

- `classification` (`allow` | `confirm_required` | `blocked`)
- `reason_code`
- `required_confirmation_level` (`none` | `operator` | `admin`)
- `policy_source`
- `audit_payload`

This keeps approvals explicit and reusable across task/CLI/research/evolution paths.
