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
