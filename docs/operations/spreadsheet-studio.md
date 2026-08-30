# Spreadsheet Studio operations

The studio is disabled by default. The bounded automatic mock path can be enabled on a Hub with:

```text
ANANTA_SPREADSHEET_STUDIO_ENABLED=true
ANANTA_SPREADSHEET_STUDIO_MODE=mock
ANANTA_SPREADSHEET_STUDIO_AUTOMATIC_PROMOTION_ENABLED=true
```

State defaults to `data/spreadsheet-studio.sqlite3`. The Angular route is `/spreadsheet-studio`; all requests
go through `/api/spreadsheet-studio`. The mock accepts canonical JSON snapshots only. XLSX, ODS, CSV,
LibreOffice rendering/recalculation and real LoRA are intentionally not advertised.

Run `python scripts/check_spreadsheet_studio_boundaries.py` and
`pytest -q tests/spreadsheet_studio`. The suite uses no network, provider, GPU, office process or person.

Production rollout additionally requires source-grounded LibreOffice/container versions, safe archive and
artifact ingestion, isolated Worker transport, recovery/retention, full formula/diff fidelity, privacy/consent,
dataset/training gates and exact `SRC_*`/`RUN_*` evidence. A mock pass cannot satisfy those gates.
