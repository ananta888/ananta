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
| Runtime claim truth and release checks | Generic runtime inventory + audit + release gate hook | `data/domain_runtime_inventory.json`, `scripts/audit_domain_integrations.py`, `scripts/run_release_gate.py` |

## Planned Blender-specific files that should remain thin

The following Blender task outputs stay Blender-specific, but should consume generic services/contracts instead of adding parallel backend control logic:

- `agent/services/blender_reference_retrieval_service.py` -> wrapper around generic domain retrieval flows.
- `agent/services/blender_action_plan_service.py` and `agent/services/blender_script_planning_service.py` -> Blender semantics only; policy/approval/audit via generic services.
- `client_surfaces/blender/bridge/ananta_blender_bridge.py` -> adapter implementation of generic bridge contract.
- `policies/blender_policy.v1.json` and Blender schemas -> domain data, not orchestration code.

## Duplication to avoid

- No separate Blender-only policy engine.
- No Blender-only approval workflow implementation.
- No Blender-only retrieval core that bypasses generic RAG profile governance.
- No Blender-side orchestration of multi-worker flows.

## Scope boundaries

- Blender keeps domain-specific context capture, UI/UX and bridge execution details.
- Hub keeps orchestration, policy decisions, approval authority, audit flow and release gate checks.
- FreeCAD/KiCad are intentionally deferred and must not be pulled into Blender MVP implementation scope.
