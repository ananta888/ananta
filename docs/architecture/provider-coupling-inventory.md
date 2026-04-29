# Provider Coupling Inventory

Scope: quick inventory of provider-specific coupling references in core-ish Hub modules (`agent/services`, `agent/routes`, runtime config/policy files).

Classification scale:
- **acceptable adapter code**: integration/client/adapter layer only
- **suspicious core leak**: core orchestration path carries provider-specific details
- **deferred/unknown**: currently tolerated, should be revisited

## Inventory

| Family | Example references (core-ish modules) | Classification | Target provider family |
| --- | --- | --- | --- |
| Worker execution backends (`opencode`, `codex`, `aider`) | `agent/services/task_scoped_execution_service.py`, `agent/routes/config/settings.py`, `agent/config_defaults.py` | **suspicious core leak** (routing/runtime specifics in core service paths) | `worker_execution` |
| Workflow automation | `agent/routes/tasks/goals.py` workflow config handling | **deferred/unknown** | `workflow` |
| MCP integrations | `agent/services/mcp_registry_service.py`, `agent/routes/mcp.py` | **acceptable adapter code** | `integration` |
| Research/evolution (`deerflow`, `evolver`) | `agent/runtime_policy.py`, `agent/services/evolution_service.py`, `agent/config_defaults.py` | **deferred/unknown** | `research` |
| Git provider integrations (`github`, `gitlab`) | `agent/routes/webhooks.py`, `agent/routes/tasks/triggers.py` | **acceptable adapter code** | `workflow` / `integration` |
| Jira/Confluence style integrations | `agent/routes/tasks/triggers.py` (`jira`) | **acceptable adapter code** (trigger adapter level) | `workflow` / `integration` |
| Domain CAD/EDA providers (`blender`, `freecad`, `kicad`) | No direct imports found in core-ish modules in this pass | **deferred/unknown** (not coupled in scanned core paths) | `domain_graph` |
| n8n/Node-RED/Activepieces/Huginn/Windmill | No direct imports found in scanned core paths in this pass | **deferred/unknown** | `workflow` |

## Immediate migration priorities

1. Move worker backend dispatch from core services toward provider registry + `worker_execution` provider contracts.
2. Keep trigger/webhook and MCP specifics in adapter surfaces only.
3. Keep research/evolution backend specifics behind a dedicated `research` provider family.

## Notes

- This inventory is intentionally conservative and focused on obvious coupling references.
- It should be rerun when expanding `core_modules_for_checks` in `config/core_provider_boundary.json`.
