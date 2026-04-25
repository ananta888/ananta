# External Coding Tool Adapters

## Purpose

External coding tools are optional worker adapters. They are never orchestration owners and never trusted by default.

## Adapter contract

Every adapter implements a common contract:

- `descriptor()` returns adapter identity, lifecycle kind (`native|optional|experimental|unavailable`) and enablement reason.
- `capabilities()` declares `plan`, `propose_patch`, `run_tests`, `apply_patch`.
- `plan(...)`, `propose_patch(...)`, `run_tests(...)` return Ananta artifacts or explicit degraded results.
- `apply_patch(...)` is optional and must not bypass Hub approval/worker patch-apply gating.

## Trust and safety model

1. Adapter output is untrusted until parsed and validated into Ananta artifact schemas.
2. Adapters cannot directly commit or apply changes to repository state.
3. Command execution must flow through worker shell policy + approval-gated executor.
4. Experimental adapters are disabled unless explicitly configured.

## SOLID alignment

- **SRP:** adapters only translate external tool output to worker artifacts.
- **DIP:** worker logic depends on the adapter interface, not concrete tool CLIs.
