# MCP and API operation allowlists

## Security model

Four independent checks remain in sequence:

1. Hub authentication establishes `agent_auth` or `user_jwt` and the existing admin state.
2. Adapter exposure, including `exposure_policy.mcp`, decides whether the transport and authentication source are reachable.
3. The operation policy resolves one stable registry ID and decides allow or deny. Unknown IDs fail closed and deny overrides allow.
4. Existing argument validation, domain authorization, approval and mutation gates still decide whether the requested work may execute.

An exposure allow is never a domain allow. Workers do not interpret or modify these policies and do not orchestrate other workers.

## Configuration

The following profile enables the current read-only MCP baseline. Write/admin operations are absent by design.

```json
{
  "operation_policy": {
    "schema_version": "1.0",
    "enabled": true,
    "revision": 0,
    "expected_revision": 0,
    "enforced_transports": ["mcp.tool", "mcp.resource"],
    "allow_operations": [],
    "deny_operations": [],
    "allow_groups": ["mcp.read.v1"],
    "deny_groups": [],
    "allowed_auth_sources": ["agent_auth", "user_jwt"],
    "require_admin_for_access_classes": ["write", "admin"],
    "require_approval_for_risks": ["high", "critical"],
    "emit_audit_events": true
  }
}
```

For the first API read rollout, add `api` to `enforced_transports` and `api.read.v1` to `allow_groups`. This gates only routes carrying the additive `operation_gate` metadata. Do not use regexes or wildcards. The accepted group IDs and their expanded operation IDs are available to administrators at `GET /governance/operations`.

To grant a mutation, add its exact operation ID or the deliberately broad `api.admin.v1`/`mcp.write.v1` group, retain admin and approval requirements, then confirm the downstream domain policy. Exact IDs are preferred.

## Listing and dispatch behavior

`GET /v1/mcp/capabilities`, `tools/list` and `resources/list` project only decisions allowed for the current authentication context. Existing MCP fields remain unchanged; `annotations` adds operation ID, access, risk, lifecycle and visible status. `tools/call` and `resources/read` resolve and check the same descriptor before constructing the dispatch context. A hidden, denied or unknown target returns the same `forbidden` shape and causes no dispatcher side effect.

## Admin inventory and clients

`GET /governance/operations` is admin-only. It supports `transport`, `access_class`, `lifecycle` and `decision` query filters. Angular settings and the TUI command `ops policy [transport=...] [access=...] [status=...]` consume this read-only DTO. Neither client evaluates or mutates policy locally.

## Revision, audit and rollback

Every persisted update needs the current `revision` or `expected_revision`. The Hub normalizes the complete candidate before mutation, stores a bounded history with actor, UTC time, hashes and a validated allow/deny diff, then uses a compare-and-swap repository write. A stale writer receives HTTP 409 instead of last-write-wins.

Rollback is `POST /config/operation-policy/rollback` with `target_revision` and `expected_revision`. The historical policy is revalidated against the current registry before it becomes a new revision. Removed or unknown operation IDs therefore cannot be restored. Audit events contain only operation IDs, transport, outcome, bounded reason/rule IDs, trace IDs and policy hashes; tokens, authorization headers, request bodies and arguments are excluded.

## Staged rollout and abort criteria

1. `disabled`: set `enabled=false`; retain the global exposure policy while observing inventory.
2. `MCP read-only`: enforce MCP transports with `mcp.read.v1`. Abort on unexpected hidden reads or any direct-call/list mismatch.
3. `API read-only`: additionally enforce `api` with `api.read.v1`. Abort on a route without declared operation metadata or changed response contracts.
4. `explicit mutations`: allow exact write/admin IDs only after domain and approval tests. Abort on a missing audit event, a denied side effect or a revision conflict without HTTP 409.

Reproduce the release evidence with:

```bash
python -m pytest -q tests/test_operation_policy.py tests/test_mcp_route.py tests/test_mcp_tool_registry.py tests/client_surfaces/operator_tui/test_operation_policy_inventory.py
cd frontend-angular && npm run test:unit -- src/app/services/operation-policy-api.service.spec.ts
python scripts/check_mcp_api_operation_allowlists.py --tests-passed
```

The final deterministic report is `artifacts/test-gates/mcp-api-operation-allowlists.json`.
