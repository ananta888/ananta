# Agent Defense-in-Depth Baseline

**Track:** `agent_defense_in_depth_adversarial_escape_safety`  
**Review basis:** static source inspection at the containing commit  
**Evidence status:** unverified for runtime containment; no `SRC_*` or `RUN_*`
assignment references were provided

## Scope

This document inventories current reusable security seams. It does not claim
that an Ananta Agent is contained after an escape, that a kill switch meets a
latency target, or that an adversarial evaluation has passed.

The Hub remains the only control plane. Workers may execute a bound safety
operation inside their own container, but they do not route work, stop other
Workers directly, or change policy.

## Capability matrix

| Capability | Current source | Status | Decision |
|---|---|---|---|
| Sandbox execution port | `agent/services/sandbox_backend.py` | Interface plus in-memory fake; no production backend in this module | REUSE the execution contract; do not claim containment |
| Command risk classification | `agent/services/sandbox_policy_service.py` | Pure normalization and class decision | REUSE for Hub admission, EXTEND with revisioned safety modes |
| Segment admission | `agent/services/segment_preflight_validator.py` | Calls the command-class policy before covered segments | REUSE; it is not a process boundary |
| Terminal authorization | `agent/services/terminal_policy_service.py` | Role/target/action policy with stable denial reasons | REUSE for terminal actions only |
| Managed SSH boundary | `agent/services/ssh_terminal_wrapper.py` | ForceCommand-style identity, target and workspace validation | REUSE as a terminal adapter, not as generic Agent containment |
| Worker process termination | `worker/training/process_control.py`, `worker/training/subprocess_executor.py` | Training-specific process-group termination | EXTRACT a narrow reusable termination adapter; do not couple Safety Core to training |
| Sandbox audit helper | `agent/services/sandbox_backend.py` | Content-reduced exec audit record builder | REUSE the redaction pattern; NEW immutable SafetyEvent contract required |
| Domain kill switches | `agent/services/sfu_broadcast_feature_policy.py`, `agent/services/semantic_media_feature_flags.py`, `agent/services/tiny_router/service.py` | Domain-local admission/fencing controls | REUSE patterns only; REJECT using any one domain switch as a global Agent stop |
| Escape regression suite | `tests/test_sandbox_escape_regression.py` | Policy classification/default tests | REUSE as policy regression; it is not runtime escape evidence |
| Sandbox operations guide | `docs/security/sandbox-operations-guide.md` | Operator guidance with explicit policy/enforcement boundary | REUSE after the clarification in this track |

## Missing safety capabilities

The current source inventory did not identify these generic, Agent-run-bound
production capabilities:

- immutable `SafetyEvent` admission and persistence
- external Hub-owned kill-switch state machine
- sandbox freeze with bounded forensic retention
- backend-independent terminate and isolate operations
- emergency network-egress fencing
- run-scoped credential lease revocation
- structured filesystem, network, process, privilege and orchestration boundary events
- incident bundle, replay contract and patch-before-retry gate
- authorized adversarial-evaluation harness and global-stop policy
- assignment-bound release evidence for any of the above

Absence in this static inventory is a planning gap, not proof that a deployed
platform control cannot exist outside this repository.

## Target seams

Use small ports rather than adding every safety responsibility to
`SandboxBackend`:

- `SandboxSafetyControlPort`: `freeze`, `terminate`, `isolate`, status receipt
- `EgressFencePort`: apply and verify a run/sandbox-scoped deny state
- `CredentialLeaseRevocationPort`: revoke only Hub-issued run credentials
- `SafetyEventRepositoryPort`: append immutable, idempotent events
- `IncidentArtifactPort`: publish bounded, redacted forensic manifests
- `SafetyTelemetryPort`: ingest closed boundary-event envelopes

A Hub-owned `AgentSafetyControlService` may coordinate these ports under one
run lock and policy revision. Concrete Worker/container adapters execute the
materialized operations. This preserves SRP, ISP and DIP and avoids a second
queue or Worker-to-Worker control path.

## Invariants

- Preventive prompting or training is never a technical isolation boundary.
- Sentinel compliance is never the sole stop mechanism.
- An Agent may request a stop but cannot modify stop authority or policy.
- Safety controls are bound to tenant, project, run, sandbox, lease and policy
  revision.
- A stale Worker receipt cannot reopen egress, credentials or execution.
- Freeze and cleanup are separate transitions with explicit bounds.
- Red-team execution is limited to Hub-authorized local test targets.
- Production and adversarial-evaluation modes use distinct policies and grants.
- No security score or passing prompt test is a containment guarantee.

## SOLID review

- **SRP:** admission, execution, safety control, credential revocation,
  telemetry and artifacts remain separate services.
- **OCP:** concrete Docker, VM or remote sandbox controls implement ports rather
  than adding backend switches to the Hub service.
- **LSP:** capability discovery must report unsupported operations; adapters
  must not return successful no-ops.
- **ISP:** execution-only backends do not depend on freeze, network or
  credential methods they cannot enforce.
- **DIP:** Hub policy depends on safety ports and immutable contracts, not on a
  container runtime or training subprocess implementation.

## Required evidence before release

Static tests may validate contracts and deterministic state machines. Runtime
claims require assignment-bound `RUN_*` evidence for the concrete deployment:

- freeze/terminate/isolate idempotency and race behavior
- egress cut-off for existing and new connections
- credential revocation and stale-lease rejection
- incident snapshot bounds and cleanup
- boundary-event completeness and redaction
- multi-Agent global-stop behavior
- original and mutated escape replay after a fix
- full isolated chaos/safety flow
