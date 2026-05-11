# Ananta Native Coding Worker

## Purpose

The native worker executes coding and shell tasks delegated by the Hub and always returns explicit artifacts instead of hidden side effects.

## Core role boundaries

- The **Hub** remains control plane owner for Goal -> Plan -> Task -> Execution -> Verification -> Artifact.
- The **worker** executes a delegated task within constraints and returns structured artifacts/results.
- External CLIs (Aider/OpenCode/ShellGPT/Copilot CLI) are optional adapters, never architecture owners.

## Worker modes

- `plan_only`
- `patch_propose`
- `patch_apply`
- `command_plan`
- `command_execute`
- `test_run`
- `verify`

## Artifact-first execution

Worker execution produces one or more of:

- PlanArtifact
- PatchArtifact
- CommandPlanArtifact
- TestResultArtifact
- VerificationArtifact

Patch and command execution paths are approval-gated when policy requires it.

## Policy, approval, audit, context and trace mapping

| Worker operation | ContextEnvelope | Policy | Approval | Audit/TraceBundle |
| --- | --- | --- | --- | --- |
| plan_only | required reference | allow or default-deny fallback | not required | trace records plan metadata |
| patch_propose | required + bounded refs | capability must permit propose | not required by default | patch hash + provenance recorded |
| patch_apply | required | allow/approval_required/default_deny enforced | required when policy demands | trace links task_id, capability_id, context_hash, approval_ref |
| command_plan | required | policy classifies command | not required | command plan artifact recorded |
| command_execute | required | deny/approval_required enforced before execution | required for approval-required/deny-like modes | stdout/stderr/exit code + trace metadata recorded |
| test_run | required | command policy + workspace constraints | depends on policy | test result artifact + trace metadata |
| verify | required | verification policy constraints | not required | verification artifact and evidence refs recorded |

## Native first, adapters second

- Native workspace, patch and verification logic is primary.
- Optional adapters must convert output into Ananta artifact schemas.
- Adapter output is untrusted until parsed, validated and policy-checked.

## Safety constraints

- No direct unbounded repo dump handling.
- No direct main-tree mutation before approval-gated apply path.
- No command execution before policy classification.
- No silent success on degraded/denied states.

---

## AWF-Track implementation status (AWF-T001–T045)

### Implemented

| Area | Gate / Module | AWF task |
|---|---|---|
| ExecutionEnvelope + PreflightGate | `worker/core/preflight.py` | T002 |
| CapabilityGrant snapshot hash | `worker/core/execution_envelope.py` | T005 |
| Mutation audit preflight | `standalone_runtime.py` | T006 |
| WorkerToolRegistry | `worker/core/tool_registry.py` | T007–T009 |
| ToolResult typed return | `worker/core/tool_registry.py` | T010 |
| ResourceLimitEnforcer | `worker/core/tool_registry.py` | T011 |
| ProviderSelectionGate + ModelPolicy | `worker/core/provider_registry.py` | T012–T013 |
| CredentialIsolationProof | `worker/core/provider_registry.py` | T014 |
| ProviderHealthGate | `worker/core/provider_registry.py` | T015 |
| ProviderProvenanceRef | `worker/core/provider_registry.py` | T016 |
| Code-context required gate | `worker/runtime/standalone_runtime.py` | T017 |
| ContextEnvelopeAdapter | `worker/core/context_bundle_adapter.py` | T018 |
| ContextBudgetGate | `worker/core/context_resolver.py` | T019 |
| ContextSensitivityFilter | `worker/core/context_resolver.py` | T020 |
| GroundedPromptAssembly | `worker/core/prompt_contract.py` | T021 |
| MemoryPolicy + redaction | `agent/services/result_memory_service.py` | T022 |
| Memory scopes | `agent/services/result_memory_service.py` | T023 |
| MemoryProposalArtifact | `agent/services/result_memory_service.py` | T024 |
| Memory provenance + trust | `agent/services/result_memory_service.py` | T025 |
| Memory TTL/expiry | `agent/repositories/memory.py` | T026 |
| SkillManifest schema | `worker/skills/skill_manifest.py` | T027 |
| SkillRegistry (disabled-by-default) | `worker/skills/skill_registry.py` | T028 |
| SkillRunner (capability-gated) | `worker/skills/skill_runner.py` | T029 |
| SkillProposalArtifact | `worker/skills/skill_proposal.py` | T030 |
| Builtin baseline skills (5) | `worker/skills/builtin/manifests.py` | T031 |
| SubworkerEnvelope | `worker/core/subworker_envelope.py` | T032 |
| DelegationArtifact | `worker/core/delegation_artifact.py` | T033 |
| Subworker depth/fan-out limits | `worker/core/subworker_envelope.py` | T034 |
| Cancellation + timeout | `worker/core/subworker_envelope.py` | T035 |
| WorkerResultV2 | `worker/core/worker_result.py` | T036 |
| TraceBundleV2 | `worker/core/trace_bundle.py` | T037 |
| Typed artifact enforcement | `worker/core/artifact_types.py` | T038 |
| WorkerDiagnosticsReadModel | `worker/core/diagnostics.py` | T039 |
| Worker audit events | `worker/core/audit_events.py` | T040 |

### Default-deny behavior

- PreflightGate: unknown capability = denied (fail-closed)
- SkillRegistry: all skills disabled by default
- SubworkerEnvelope: child cannot escalate beyond parent capabilities
- ProviderSelectionGate: cloud denied when `cloud_allowed=False`
- ContextSensitivityFilter: confidential/secret blocked for cloud dispatch
- Memory: raw output archive off by default; secrets redacted before persist
- Mutation audit: fail-closed (T006)

### Developer test commands

```bash
# All AWF tests
python -m pytest tests/test_awf_worker_fixup_t001_t010.py tests/test_awf_worker_fixup_t011_t020.py tests/test_awf_worker_fixup_t021_t030.py tests/test_awf_worker_fixup_t031_t045.py -q

# Security regression suite only
python -m pytest tests/test_worker_security_regression.py -q

# Single batch
python -m pytest tests/test_awf_worker_fixup_t031_t045.py -q
```
