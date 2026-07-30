# Model-intelligence contract fixtures

`contracts.v1.valid.json` and `contracts.v1.invalid.json` are minimal,
deterministic OWMA-002 wire fixtures. They contain no timestamps, random IDs,
host paths, model weights, or live-run output.

The same files are consumed by:

- `tests/test_model_intelligence_contracts.py`
- `frontend-angular/src/app/contracts/model-intelligence.contract.spec.ts`

This shared source makes Python/JSON-Schema/TypeScript contract drift visible.
