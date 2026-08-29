# ADR: additive dendritic-memory experiments

Status: accepted for a default-off experimental implementation.

## Decision

`dendritic_memory_experiment` is a separate Hub-owned job, Worker contract and
artifact lifecycle. It does not extend the meaning of LoRA/QLoRA v2 payloads,
does not register a Memory Pack as a normal LoRA adapter and does not change
the existing LoRA runtime route.

The Hub owns admission, idempotency, tenant scope, attempt fencing, evaluation,
registry transitions, composition, runtime policy and audit. The isolated
Worker receives exactly one materialized job and cannot create Hub tasks,
delegate to another Worker, approve a pack or activate runtime state.

The dependency-free contracts live in `ananta_contracts`; Torch and
Safetensors remain lazy imports below `worker/training/dendritic`. This protects
SRP and DIP: Hub policy depends on closed data contracts and ports, while the
optional ML implementation is replaceable.

## Safety and compatibility

- Default configuration is disabled.
- Memory Packs are always `experimental=true`, `production_eligible=false`
  and `claims_verified=false` in v1.
- Pickle, free imports, remote code, remote downloads and public file paths are
  outside the contract.
- All positive decisions can execute automatically after deterministic gates;
  neither production automation nor tests require a human.
- Removing the feature flag leaves existing LoRA rows, APIs and artifacts
  unchanged and requires no LoRA data migration.
