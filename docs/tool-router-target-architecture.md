# ToolRouter Target Architecture (hub-governed)

## Goal

Provide one reusable routing subsystem for tool/backend selection so execution paths stop duplicating conditional routing logic.

## Control-plane ownership

- Routing policy is evaluated in hub services.
- Workers execute delegated decisions but do not decide routing policy.
- Fallback decisions are policy-scoped and explainable.

## Routing inputs

1. Task intent (`task_kind`, requested operation class, sensitivity).
2. Capability catalog (tools/backends and declared capabilities).
3. Governance profile (safe/balanced/strict + approval constraints).
4. Runtime availability (preflight health, fallback eligibility).
5. Policy overrides (task/role/template constraints).

## Normalized capability catalog contract

Each tool/backend entry should expose:

- `id`
- `kind` (`tool` | `backend`)
- `capability_classes[]` (for example: `research`, `patching`, `shell_execution`, `planning`, `review`, `admin_repair`)
- `risk_class` (`low` | `medium` | `high`)
- `supports_stateful_session` (bool)
- `requires_approval` (bool)
- `availability` (`ready` | `degraded` | `unavailable`)

The same catalog is consumable by routing and approval services.

## ToolRouter service contract

`ToolRouterService.route(request) -> decision`

- request includes task metadata, governance mode, capability constraints and preferred backend/tool hints
- decision includes selected target, ranked alternatives and policy checks
- decision includes fallback handling outcome (`used`, `blocked`, `not_needed`)

## Decision explainability output

Router decisions should include:

- `selected_target`
- `selected_reason`
- `alternatives[]` with rejection reasons
- `policy_checks[]` (rule/result/reason)
- `governance_mode`
- `availability_snapshot`

This output is intended for traces/read-model surfaces in operator views.

## Current diagnostics surface

Routing diagnostics are exposed in runtime payloads under:

- `routing.decision_chain` (policy path and selected source)
- `routing.fallback_policy` (effective fallback gates)
- `routing.tool_router.decision` (selected target, alternatives, policy checks)
- `routing.tool_router.catalog_summary` (normalized catalog counts/classes)
