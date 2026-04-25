# Blender Generic Domain Alignment

## Goal

Map `todo.blender.json` delivery to the generic domain foundation so Blender stays a thin client/bridge surface and does not duplicate hub-owned backend logic.

## Mapping from Blender track to generic mechanisms

| Blender scope | Generic mechanism to reuse | Current foundation asset |
| --- | --- | --- |
| Capabilities and safety flags | Generic capability registry + policy service | `agent/services/capability_registry.py`, `agent/services/domain_policy_service.py` |
| Scene/artifact contract validation | Generic context/artifact schema validation | `agent/services/context_schema_registry.py`, `agent/services/artifact_type_registry.py` |
| RAG source ingestion/retrieval | Generic RAG profile loader + domain retrieval service | `agent/services/rag_source_profile_loader.py`, `agent/services/domain_retrieval_service.py` |
| Bridge operation envelope | Generic bridge adapter contract + registry | `schemas/domain/bridge_adapter_contract.v1.json`, `agent/services/bridge_adapter_registry.py` |
| Action routing and approval gate | Generic domain action router skeleton | `agent/services/domain_action_router.py` |
| Runtime claim truth and release checks | Generic runtime inventory + audit + release gate hook | `data/domain_runtime_inventory.json`, `scripts/audit_domain_integrations.py`, `scripts/release_gate.py` |

## Planned Blender-specific files that should remain thin

The following Blender task outputs stay Blender-specific, but should consume generic services/contracts instead of adding parallel backend control logic:

- `agent/services/blender_reference_retrieval_service.py` -> wrapper around generic domain retrieval flows.
- `agent/services/blender_action_plan_service.py` and `agent/services/blender_script_planning_service.py` -> Blender semantics only; policy/approval/audit via generic services.
- `client_surfaces/blender/bridge/ananta_blender_bridge.py` -> adapter implementation of generic bridge contract.
- `policies/blender_policy.v1.json` and Blender schemas -> domain data, not orchestration code.

## Blender task ownership alignment (generic service first)

| Task ID | Blender artifact | Generic owner/service |
| --- | --- | --- |
| CLEAN-T10 | `client_surfaces/blender/addon/__init__.py`, `client_surfaces/blender/bridge/ananta_blender_bridge.py` | Hub/task orchestration remains in `agent/services/task_scoped_execution_service.py`; Blender files only shape bridge envelopes. |
| CLEAN-T11 | `scripts/run_blender_smoke_checks.py`, `ci-artifacts/domain-runtime/blender-smoke-report.json` | Runtime claim truth remains in `data/domain_runtime_inventory.json` + `scripts/audit_domain_integrations.py`. |
| CLEAN-T12 | this alignment document | Ownership boundaries are tracked here and reviewed against generic registry/policy/retrieval services before Blender task promotion. |

## Promotion criteria from foundation_only to runtime_mvp

The Blender inventory status can be promoted only when all of the following are true:

1. Domain action execution path is hub-routed (`task_kind=domain_action`) and policy/approval-enforced.
2. Blender smoke report is generated from `scripts/run_blender_smoke_checks.py` and published as release evidence.
3. Inventory, descriptor lifecycle/runtime status and audit output agree on a runtime claim with no blockers.

## Duplication to avoid

- No separate Blender-only policy engine.
- No Blender-only approval workflow implementation.
- No Blender-only retrieval core that bypasses generic RAG profile governance.
- No Blender-side orchestration of multi-worker flows.

## Scope boundaries

- Blender keeps domain-specific context capture, UI/UX and bridge execution details.
- Hub keeps orchestration, policy decisions, approval authority, audit flow and release gate checks.
- FreeCAD/KiCad are intentionally deferred and must not be pulled into Blender MVP implementation scope.
