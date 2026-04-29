# Standalone Worker Quickstart

## 1) Manifest erstellen

```json
{
  "schema": "standalone_task_contract.v1",
  "task_id": "AW-STANDALONE-1",
  "goal": "Run bounded command",
  "command": "pytest -q",
  "worker_profile": "balanced",
  "files": ["src/app.py"],
  "diffs": [],
  "control_manifest": {
    "trace_id": "tr-standalone-1",
    "capability_id": "worker.command.execute",
    "context_hash": "ctx-standalone-1"
  }
}
```

## 2) CLI ausführen

```bash
python -m worker.cli.standalone_worker_cli \
  --workspace . \
  --manifest ./manifest.json \
  --output ./standalone-result.json
```

## 3) Ergebnis lesen

Ausgabe enthält:

- `result` (Status, task_id, reason)
- `artifacts` (maschinenlesbare Artefaktliste)
- `trace_events` (chronologische Laufzeitereignisse)

