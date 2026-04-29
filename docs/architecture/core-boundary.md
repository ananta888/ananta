# Core Boundary Definition

This document defines the conservative package boundary used for the core/provider migration.

Machine-readable boundary config: `config/core_provider_boundary.json`

## Zones

1. **Core**
   - Owns task/plan/policy/audit/orchestration state.
   - Must not depend on provider-specific internals.
   - Primary paths: `agent/routes`, `agent/services`, `agent/runtime_policy.py`, `agent/runtime_profiles.py`.
2. **Provider Interface**
   - Stable provider-neutral contracts and registry only.
   - Primary paths: `agent/providers/interfaces.py`, `agent/providers/registry.py`, `agent/providers/domain_graph.py`, `agent/providers/workflow.py`, `agent/providers/worker_execution.py`, `agent/providers/redaction.py`, `agent/providers/provenance.py`.
3. **Provider Implementation**
   - Optional backend-specific adapters/factories.
   - Primary paths: `agent/services/workflow_providers`, `worker/adapters`.
4. **Client Adapter**
   - Transport/UI/API entrypoints that consume core contracts.
   - Primary paths: `agent/routes`, `agent/cli`.

## Dependency direction

Allowed:

```text
Core -> Provider Interface
Provider Implementation -> Provider Interface
Client Adapter -> Core + Provider Interface
```

Forbidden:

```text
Core -> Provider Implementation internals
Core -> backend-specific provider packages (n8n/blender/freecad/kicad/opencode/etc.)
Provider Implementation -> direct mutation of core task/policy state
```

## Existing module mapping (conservative)

| Module/path | Zone | Note |
| --- | --- | --- |
| `agent/services/task_delegation_services.py` | Core | Hub delegation + context orchestration |
| `agent/services/task_scoped_execution_service.py` | Core (migration hotspot) | Still contains backend-specific routing details; migration target is provider interfaces |
| `agent/routes/tasks/*.py` | Client Adapter | API orchestration surfaces |
| `agent/providers/*.py` | Provider Interface | Provider-neutral contracts and registry |
| `worker/adapters/*_adapter.py` | Provider Implementation | Backend-specific worker adapters |
| `agent/services/mcp_*` | Provider Implementation | Integration-specific adapter logic |

## Why conservative

The boundary intentionally starts narrow to avoid big-bang refactors.  
Static checks run only against `core_modules_for_checks` from `config/core_provider_boundary.json` and can be expanded incrementally.
