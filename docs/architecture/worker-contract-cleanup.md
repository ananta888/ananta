# Worker Contract Cleanup

This repository keeps the hub as the authoritative control plane.
The worker stack must therefore share one coherent contract across policy,
selection, execution, and configuration.

## Tool policy

- An empty `ToolPolicy.allowed_tool_ids` is fail-closed by default.
- Legacy permissive behavior exists only via `legacy_default_allow=True`.
- `PreflightGate`, `MCPAdapter`, and the tool-policy tests must all use the same semantics.

## Worker taxonomy

- `Hermes` is treated as an external analysis worker, not as a cloud-class worker kind.
- Cloud gating remains a runtime-target concern.
- Policy helpers should not encode conflicting cloud and external meanings for the same worker kind.

## Strategy contract

- `WorkerStrategy` consumes a structured selection decision.
- Missing optional reason-code metadata must not crash strategy execution.
- Structured worker output normalizes to executable proposals.
- Prose output stays advisory.
- Empty output remains an explicit decline with diagnostics.

## Parallelism contract

- The Ollama parallelism baseline must be defined in one place and mirrored by:
  - `config/worker_parallelism.default.json`
  - `agent/config_defaults.py`
  - schema validation
  - regression tests
- Diverging defaults are treated as a contract bug, not a harmless tuning detail.

## Test policy

- Tests should express the authoritative contract, not the historical one.
- When a compatibility path is kept for legacy reasons, it must be explicit and documented.
