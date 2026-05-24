# Terminal Authorization Matrix

Terminal access is a dedicated authorization surface and is deny-by-default.

## Permission classes

- `terminal.worker.*` for worker terminals
- `terminal.hub.*` for direct hub terminals (high-risk)
- `terminal.hub_as_worker.*` for hub runtime acting as worker

## Deterministic policy decisions

Every decision includes:
- `decision_id`
- `allow`
- `reason_code`
- `policy_version`
- `permission`
- `matched_rule_id` (for allow decisions)

## Security invariants

- `terminal.worker.attach` does not imply `terminal.hub.attach`
- `terminal.worker.write` does not imply `terminal.hub.write`
- hub terminal access remains denied unless explicitly granted
- unknown operation is denied with `terminal_operation_unknown`

## Reason codes

- `terminal_permission_granted`
- `terminal_permission_denied`
- `terminal_hub_access_denied_default`
- `terminal_operation_unknown`
