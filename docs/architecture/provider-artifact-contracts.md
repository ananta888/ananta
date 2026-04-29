# Provider-Neutral Artifact Contracts

This map defines boundary objects exchanged between Hub/Core and provider families.

## Contract map

| Provider family | Neutral contract(s) | Current schema state | Notes |
| --- | --- | --- | --- |
| `domain_graph` | `DomainGraphArtifact` | `schemas/artifacts/domain_graph_artifact.v1.json` | Provider-specific scene/netlist/document internals must stay behind this contract. |
| `workflow` | `WorkflowIntegrationRunArtifact` | **gap** (planned in workflow track) | Use neutral status/provenance payload; keep provider callback raw payload redacted. |
| `worker_execution` | `worker_todo_contract.v1`, `worker_todo_result.v1`, `patch_artifact.v1`, `verification_artifact.v1` | Present (`schemas/worker/*`) | Worker backend specifics stay in provider adapters, not in Hub core flow. |
| `research` | `EvolutionProposalArtifact` / research run artifacts | Partial (existing evolution/research artifacts in services; no unified schema here) | Promote to shared schema when mature. |
| cross-family | provenance metadata | Helper: `agent/providers/provenance.py` | Standard fields: provider_id, provider_family, provider_version, external_ref, source_ref, run_id, trace_id. |
| cross-family | payload redaction | Helper: `agent/providers/redaction.py` | Secret-looking keys and configured secret refs must be redacted before logs/artifacts. |

## Raw payload rule

Provider-specific raw payloads may be stored only behind metadata/raw payload sections after redaction.  
Core policy/state/audit/UI should consume the neutral contract surface, not provider-native payload formats.

## Identified gaps

1. Dedicated schema for `WorkflowIntegrationRunArtifact`.
2. Unified schema for evolution/research proposal artifacts.
3. Wider adoption of shared provenance helper in all provider adapters.
