# Agent Defense-in-Depth

## Authority and responsibility

The Hub owns safety policies, run admission, group-stop decisions, immutable
events, incident disposition and replay admission. Workers execute only the
specific safety operation delegated for their own container. They do not stop
other Workers, create independent queues or change policy.

The implementation deliberately separates these interfaces:

- `SandboxSafetyControlPort`: freeze, terminate or isolate a bound sandbox;
- `EgressFencePort`: deny network egress for that run and sandbox;
- `CredentialLeaseRevocationPort`: revoke Hub-issued run credentials;
- `AgentSafetyStateStorePort`: persist immutable revisions and events;
- `AgentSafetyControlService`: coordinate decisions under Hub authority.

This protects SRP and ISP. Runtime adapters can be replaced without changing
policy semantics (OCP/LSP), and the Hub depends on ports rather than container
technology (DIP).

## Independent layers

1. Preventive policy classifies and rejects disallowed requests.
2. Optional behavior training influences model behavior but grants no authority.
3. Technical isolation constrains filesystem, process, privilege and network access.
4. Signed sentinel manifests add open or opaque trigger experiments.
5. External detectors record boundary outcomes independently of Agent reports.
6. The Hub stop pipeline fences execution, egress and credentials.
7. Incident bundles preserve redacted, digest-bound evidence.
8. Patch-before-retry and local replay turn confirmed findings into regressions.

No layer is described as a complete alignment or containment guarantee.

## Modes and automated admission

| Mode | Prevention | Telemetry / external stop | Target rule |
| --- | --- | --- | --- |
| `enforce` | enabled | mandatory | policy-bound production target |
| `observe_only` | configurable | mandatory | policy-bound target |
| `adversarial_eval` | may be disabled | mandatory, high fidelity | exact authorized `local:*` target only |
| `disabled` | optional track features disabled | platform controls remain independent | no implicit permission expansion |

Admission and rejection are fully automatic. No test or production path waits
for a person. Organizational deployment policy may withhold configuration, but
the runtime outcome is a deterministic denial rather than a pending HITL gate.

## Sentinel contract

A manifest binds tenant, project, run, sandbox, policy revision and mode,
manifest version, absolute priority, nonce, validity window, trigger class and
effect with an HMAC signature. The Hub stores
the issued manifest outside workspace-controlled prompt artifacts. A trigger is
single-use. Its immutable event is appended before containment starts.

Open triggers are `shutdown`, `boundary_breached`, `success_report` and
`trace_checkpoint`. An opaque trigger uses a random symbol, rotates per series
and exposes only the priority rule to the training record. Train and holdout
symbols are mandatory and separated. The mechanism is experimental and never
the only stop path.

## Stop and group policy

`agent`, `sandbox` and `run` scopes operate on the bound run. `group` selects
active runs with the same Hub-owned group ID. Every sandbox gets both a control
and egress receipt, and every run gets a credential-revocation receipt. Any
unsupported or negative receipt leaves all selected runs non-executable with a
failed-closed state.

The persisted operation contains no credential material. Agent self-report and
external observation stay distinguishable by event source.

## Incident, replay and regression

An incident bundle includes policy/runtime binding digests, ordered event
digests, the control-operation digest, bounds and redaction state. Root causes
use a closed multi-select vocabulary:

- model/policy generalization;
- prompt/task specification;
- sandbox configuration;
- runtime vulnerability;
- credential/access control;
- monitoring gap.

A local replay is rejected until the incident is classified as patched or as
an explicitly isolated red-team retry. Patched dispositions require a patch
digest. Both the exact replay and mutated variants retain the source bundle
digest; deleting or broadly mocking a regression is not a valid fix.

## Metrics and rollout

Dashboards report boundary outcomes by class, self-report/external-observation
differences, containment receipt failures, trigger compliance, stop latency,
incident count and replay coverage. They must not combine these into a single
claim of safety.

Rollout proceeds through `observe_only`, isolated canary and enforced profiles.
Exit criteria are source-bound passing contract/security tests plus real
deployment `RUN_*` receipts for the concrete adapters. Rollback changes the
active policy revision; immutable events and incidents remain available.
