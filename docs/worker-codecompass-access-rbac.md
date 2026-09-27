# Worker CodeCompass access and role-based permissions

**Track:** WCRB (`todos/active/todo.worker-codecompass-access-rbac.json`)
**SOLID-Bezug:** SRP (issuer, delivery, gateway, artifact transfer are separate
services), OCP (access paths are configuration, not code branches in the tools),
DIP (gateway and materializer take their collaborators as ports).

Workers use CodeCompass tools in their tool loop, but they must never get
more access than the person who asked for the task. The Hub stays the owner of
policy, index and identity; a worker only ever holds a closed, signed
projection of what the Hub decided.

## 1. Roles and bindings

Roles are stored in the Hub database and managed through the admin API
(`/access/*`: roles, bindings, revisions, rollback; every change is audited
and snapshotted as a revision).

| Role | Grants |
|------|--------|
| `viewer` | read groups: status, tasks, artifacts, CodeCompass and repository reads |
| `developer` | viewer + `ananta.tool.execution.v1` (tests, allowlisted commands) |
| `maintainer` | developer + `ananta.tool.write.v1` and `mcp.write.v1` |
| `admin` | everything |

Built-in roles are code-owned and read-only; custom roles use the same grant
document (`allow_groups`, `allow_operations`, `deny_*`, `constraints`: `paths`,
`projects`, `index_ids`).

A binding attaches a role to a subject, optionally per tenant/project:

- `local_user` - a Hub user name;
- `oidc_group`, `oidc_realm_role`, `oidc_client_role` - Keycloak groups, realm
  roles and client roles, taken from the OIDC token at every login and stored
  on the identity link.

`GET /access/me` shows the effective roles and grants of the caller.

Worker tools are operations `ananta.tool.<tool>` in the groups
`ananta.tool.read.v1`, `ananta.tool.write.v1` and `ananta.tool.execution.v1`,
so the same grants decide MCP tools, APIs and worker tools.

## 2. From request to tool call

1. **Requester (WCRB-006).** At ingest, a task records `requested_by_subject`,
   `requested_by_tenant` and `requested_roles` from the authenticated request.
   Derived tasks inherit them through their parents at use time.
2. **Capability (WCRB-007).** The Hub issues a CodeCompass capability for the
   requester: repository, revision, paths and index ids (from role
   constraints), the operations the roles allow, the task and the receiving
   worker (`audience`). It is sealed and signed with a key only the Hub holds
   (`ANANTA_CODECOMPASS_CAPABILITY_KEY_FILE`, else derived from the Hub secret).
   Work without requester roles (system work) is unrestricted.
3. **Tool arguments** never carry authority: a capability, token or collection
   in model arguments is refused (`client_authority_forbidden`); the trusted
   capability is injected from the tool-loop configuration only.

## 3. The two access paths

Configured in the Hub config section `ananta_worker_tool_loop`:

```json
{
  "codecompass_access": "delegated",
  "codecompass_access_overrides": {"codecompass.architecture_diagram": "hub"},
  "codecompass_access_fallback": "none"
}
```

| Mode | Where the tool runs | Notes |
|------|--------------------|-------|
| `delegated` (default) | on the worker (path B) | needs the capability sent with the step |
| `hub` | on the Hub (path A) | the worker calls the Hub gateway |
| `off` | nowhere | CodeCompass tools return `codecompass_access_off` |

`codecompass_access_fallback: "hub"` lets a delegated call without a
capability use the Hub instead; the default (`none`) fails closed.

### Path B - delegated (WCRB-009, WCRB-010)

- The Hub's forwarder attaches its own capability to each step for the assigned
  worker; a capability arriving from anyone else is dropped. The worker accepts
  it only from a service-authenticated request and keeps it for that step
  (context variable, never a workspace file).
- Graph tools need the Hub's graph artifacts, which live in the Hub volume.
  On `graph_artifact_not_materialized`, the worker fetches the file from
  `GET /api/codecompass/worker-artifacts/<sha256>`:
  - registered worker (scope `codecompass.artifacts.read`);
  - header `X-Ananta-CodeCompass-Capability` with a Hub-signed capability whose
    `audience` is this worker;
  - only artifacts bound by an index in the capability's `allowed_index_ids`.

  The file is stored at its admitted path inside the worker's own
  `data/knowledge_indices` (graph and metrics stay side by side); both the
  fetch and the unchanged resolver verify the sha256.
- The capability's index ids also narrow which index a graph tool may open.

### Path A - Hub gateway (WCRB-008)

`POST /api/worker/v1/tasks/<task_id>/tools/<tool>` with
`{"arguments": {...}, "tool_call_id": "..."}` (registered worker, scope
`codecompass.tools.execute`). The Hub admits the call only if the task is
active and assigned to the calling worker, the tool is a read-only CodeCompass
tool on the worker allowlist, and the requester's roles allow it. It then
issues its own capability and runs the tool bounded
(`ANANTA_WORKER_TOOL_GATEWAY_TIMEOUT_SECONDS`, default 45 s) and audited
(`worker_tool_gateway_called`). Refusals come back to the model as coded tool
errors (`hub_gateway_<reason>`), never as a local fallback.

The Meet companion (`/internal/assist/tool`) shares the bounded execution and
audit (`agent/services/hub_tool_gateway.py`) but keeps its own admission.

## 4. Code map

| Concern | Module |
|---------|--------|
| roles, grants, bindings | `agent/services/access_roles.py`, `access_role_admin_service.py`, `routes/access_roles.py` |
| principal and Keycloak memberships | `agent/services/access_principal.py`, `oidc_claims_mapper.py` |
| requester | `agent/services/task_requester.py` |
| capability issue/verify | `agent/services/codecompass_capability_issuer.py` |
| capability per step, access modes | `agent/services/codecompass_task_capability.py` |
| path B artifacts | `agent/services/codecompass_worker_artifacts.py`, `routes/codecompass_worker_artifacts.py` |
| path A gateway | `agent/services/worker_tool_gateway.py`, `routes/worker_tool_gateway.py`, `codecompass_hub_tool_client.py` |
| routing per tool call | `agent/services/tools/__init__.py` (`_codecompass_route`) |

## 5. Preserved debt

- `agent/services/tools/__init__.py::_dispatch_ananta_tool` is a long `if`
  chain (C901, OCP): a registry table of executors would be the cleaner form.
  Left unchanged to keep this change small.
