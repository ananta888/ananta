# Contract Schema Baselines (CNT-031)

To ensure API and read-model stability, Ananta uses JSON schema baselines for core contracts.

## Overview

The `SystemContractService` defines a catalog of JSON schemas for all critical models (OpenAI compatibility, Governance, Task management, etc.).
These schemas are stored as snapshots in `tests/baselines/schemas/`.

## Running the Verification

To verify that the current code still matches the established baselines, run:

```powershell
$env:PYTHONPATH="."
pytest tests/test_contract_schemas.py
```

## Updating Baselines

If you intentionally change a model and want to update the baselines, run the test with the `ANANTA_UPDATE_BASELINES` environment variable set to `true`:

```powershell
$env:PYTHONPATH="."
$env:ANANTA_UPDATE_BASELINES="true"
pytest tests/test_contract_schemas.py
```

Alternatively, running the script directly also updates the baselines:

```powershell
$env:PYTHONPATH="."
python tests/test_contract_schemas.py
```

## Adding New Contracts

To add a new contract to the baseline system:

1. Ensure the model is a `SQLModel` or `pydantic.BaseModel` in `agent/models.py`.
2. Add the model to `agent/services/system_contract_service.py` in the `build_contract_catalog` method.
3. Run the update command as described above.
