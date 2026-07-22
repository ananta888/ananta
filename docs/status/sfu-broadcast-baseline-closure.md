# SFU Broadcast Baseline Closure Boundary

This document records the code-side boundary for SFB-BASE-001 through
SFB-BASE-009. It is not production evidence and it does not override the
parent ASMP decision. The effective state remains no_go / observe_only.

## Effective boundary

- The Hub remains the only activation, admission, feature-policy and rollback
  authority. Runtime agents execute bounded commands and do not orchestrate.
- Runtime control is closed to livekit_control_api,
  authenticated_runtime_extension, or unsupported. A selected mode without
  verified runtime evidence is effectively unsupported.
- LiveKit native scheduling remains the placement owner. The Hub owns policy,
  membership, admission, audience, epochs, fencing and failover decisions.
- Static feature defaults remain false. Persistent versioned policy may narrow
  behavior but a client or runtime process cannot widen it.
- The conservative baseline admits zero SFU nodes and zero participants until
  all run budgets and grounded evidence pass.
- No source or run identifier is created by the baseline boundary. Only IDs
  already supplied by a trusted evidence registry can be verified.
- Rollback is a finite Hub-owned sequence: fence, stop admission, disable
  optional features, project the parent fallback, drain and verify quiescence.

## Baseline item state

| ID | Code/config/doc state | Remaining real blocker |
| --- | --- | --- |
| SFB-BASE-001 | Partial | The tracked audit artifact must be regenerated from the final source commit and validated; existing external runtime findings must not be inferred. |
| SFB-BASE-002 | Done code-side | Parent ASMP remains no_go / observe_only; this repository cannot change that decision. |
| SFB-BASE-003 | Done code-side | A fresh, monotonic, digest-bound parent go manifest for an active stage is absent. |
| SFB-BASE-004 | Done code-side | Production enablement remains forbidden while parent readiness and evidence are blocked. |
| SFB-BASE-005 | Partial | Every declared profile needs repeated real load evidence with confidence, variance, reserve, retry, cleanup and leak-trend measurements. |
| SFB-BASE-006 | Partial | A pinned-image publisher plus three-receiver container smoke and capability drift evidence are absent. |
| SFB-BASE-007 | Partial | Fresh manifests with real source/run references and, where configured, valid attestations are absent. |
| SFB-BASE-008 | Partial | The persistent repository contract exists; production upgrade/downgrade and database-unavailable evidence still has to be produced by the deployment environment. No migration is added by this package. |
| SFB-BASE-009 | Partial | The Hub rollback boundary exists; real multi-Hub, legacy fallback and graceful-drain evidence plus central bootstrap wiring are intentionally outside this package. |

## Machine-readable files

- config/sfu_broadcast_baseline_activation.default.json is the fail-closed
  current projection.
- config/sfu_broadcast_baseline_limits.v1.json defines units, windows,
  percentiles, ranges, missing-data behavior and all required run profiles.
- schemas/sfu-broadcast-baseline-activation.schema.json and
  schemas/sfu-broadcast-baseline-limits.schema.json constrain those files.
- agent/services/sfu_broadcast_activation_boundary.py combines parent,
  runtime, feature, limit and grounding projections without hidden state.
- agent/services/sfu_broadcast_source_grounding.py rejects unknown evidence
  references and deliberately exposes no identifier factory.
- agent/services/sfu_broadcast_rollback_service.py defines the narrow rollback
  port and bounded Hub-side execution sequence.

## SOLID check

The activation evaluator, limit qualification, source grounding and rollback
execution are separate services (SRP). Infrastructure is behind a narrow
rollback protocol (DIP/ISP), and the changes are additive without changing the
Hub-worker ownership model (OCP/LSP). No new global mutable state or implicit
worker-to-worker path is introduced.
