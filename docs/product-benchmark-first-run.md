# First Product Benchmark Evidence Run

Date: 2026-04-21

This is the first documented benchmark evidence run for the product benchmark suite. It validates the benchmark catalog, scoring model, release narrative fields, tool contracts, worker capability profiles, backend provider contracts and third-party integration rules as a reproducible local evidence baseline.

This run is intentionally scoped as contract and catalog validation. It does not claim live end-to-end task performance against a running Ananta deployment.

## Scope

| Field | Value |
| --- | --- |
| Suite version | `v1` |
| Task count | 5 |
| Score total | 100 |
| Evidence level | Local contract validation |
| Live execution benchmark | Pending |

## Commands

```bash
python3 -m py_compile agent/backend_provider_contracts.py agent/integration_guidelines.py agent/product_benchmark_suite.py
timeout 240s .venv/bin/pytest tests/test_backend_provider_contracts.py tests/test_integration_guidelines.py tests/test_product_benchmark_suite.py tests/test_tool_capabilities_contract.py tests/test_worker_capability_profiles.py -vv
```

## Result

| Check | Result |
| --- | --- |
| Python compilation | Passed |
| Contract and benchmark tests | 11 passed in 72.14s |

## Release Narrative

| Field | Evidence |
| --- | --- |
| `headline` | Benchmark contract suite `v1` is defined and locally validated. |
| `best_signal` | Five fixed product tasks, a 100-point scoring model and stable comparison targets are available for repeatable release retrospectives. |
| `governance_signal` | Governance quality and block quality are first-class benchmark criteria, and integration rules keep extension paths inside the hub-owned orchestration model. |
| `regression_watch` | Live execution timing and real task outcome evidence still need a deployment-backed benchmark run before making performance claims. |
| `evidence_links` | `agent/product_benchmark_suite.py`, `docs/product-benchmark-suite.md`, `tests/test_product_benchmark_suite.py`, `docs/backend-provider-contracts.md`, `docs/third-party-integration-guidelines.md` |

## Follow-Up For Live Runs

A live benchmark run should reuse the same task IDs, profile, governance mode and evidence level. The release decision record should then link the concrete run artifact, workflow run or log bundle next to this baseline.
