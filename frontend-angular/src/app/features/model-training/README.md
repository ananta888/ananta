# Modelltraining-Feature

Das lazy geladene Admin-Feature unter `/model-training` ist das Angular-Control-
Center für lokale LoRA-/QLoRA-Datasets, Jobs, Evaluationen und Adapter. Es
spricht ausschließlich `/api/ml-intern-training` und die Hub-seitige
Runtime-Management-API an; Worker-URLs und Serverpfade gehören nie in DTOs.

Aufteilung:

- `datasets/`: Import, Katalog, paginierte Train-/Validation-Preview,
  Split/External-Validation und Validierungsreport
- `training-wizard/`: capability- und limitgebundene Konfiguration,
  Dry-run/Live-Bestätigung und idempotenter Submit
- `jobs/` plus `model-training-job-monitor.service.ts`: Historie, Filter,
  monotone Events, Polling/SSE-Fallback, Metriken und Cancel-Zustände
- `evaluation/`: Base-vs-Adapter-Report und bounded Samples
- `adapters/`: sicherer Import, Registry-Lifecycle, Export, Unload und Rollback
- `model-training-api.service.ts`: typisierte Hub-API
- `model-training.facade.ts`: Featurezustand und Orchestrierung der UI

Visual-Process-Schritte verwenden dieselben Dataset-IDs und Hub-Jobverträge.
Legacy-Pfadfelder werden nur read-only/deprecated dargestellt und über den
serverseitigen Quarantäneadapter migriert.

Lokale Gates:

```bash
cd frontend-angular
npm run test:unit
npm run build
npm run lint
npx playwright test tests/model-training.spec.ts --project=chromium
```

Die Betriebs- und Sicherheitsregeln stehen in
[`../../../../../docs/operations/lora-training.md`](../../../../../docs/operations/lora-training.md).
