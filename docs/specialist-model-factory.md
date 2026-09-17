# Gate + Benchmark Model Factory (Specialist Decision Models)

Track: `todos/active/todo.gate-benchmark-specialized-model-factory.json`

Deterministische Gates, Benchmark-Oracles und die vorhandene ML-Intern
LoRA/QLoRA-Infrastruktur bilden einen geschlossenen Post-Training-Kreislauf:

```text
Gate-/Benchmark-Läufe ──► TrainingExample (provenance, contract_version, label_source)
        │                        │ Redaction · Dedup · Quarantäne
        │                        ▼
        │                 Train / Validation / Holdout (gruppenweise, Holdout eingefroren)
        │                        │ Leakage-Gate = Admission je Training-Job
        │                        ▼
        │        CreateTrainingJobCommand (task_family = specialist_decision, ML-Intern v2)
        │                        │ LoRA / QLoRA über vorhandene Backends
        │                        ▼
        │              BenchmarkResult (Accuracy, Macro-F1, False-Allow/False-Deny, Ressourcen)
        │              CalibrationReport (Brier, ECE, Abstain-Schwelle aus Validation)
        │                        ▼
        │              PromotionDecision (Hard-Minimums, No-Regression, Provenance)
        │                        ▼
        └──── Fehlerfälle ◄── ML-Intern Adapter Registry (approve / reject / rollback)
```

## Bausteine

| Task | Modul | Inhalt |
|---|---|---|
| GBMF-001 | `ananta_contracts/specialist_decision.py` | `SpecialistDecisionContract`: typisierter Input, geschlossene Labels, Safety-Rollen (allow/deny), abstain/escalate, optionale Confidence; `builtin_contracts()` für tool-router, code-risk-gate, plan-validator, structured-output-repair, retrieval-relevance, task-completion-gate. Freitext ist nie der Maschinenvertrag. |
| GBMF-002 | `agent/services/specialist_training_examples.py` | `SpecialistExampleAdmission`: Provenance, `label_source` (deterministic_gate / policy_rule / benchmark_oracle / human+reviewed), Secret-/PII-Redaction (Private Keys blockieren), Dedup über Content-Digest, Quarantäne widersprüchlicher deterministischer Labels. |
| GBMF-003 | `agent/services/specialist_dataset_split.py` | Gruppenweiser, seed-deterministischer Split; `FrozenHoldout` mit Digest; `check_leakage` (exakt / Input / Gruppe); `admit_training_partitions` als Job-Admission; spätere Dataset-Versionen behalten den eingefrorenen Holdout. |
| GBMF-004 | `agent/services/specialist_training_task_family.py`, `ml_intern_training_contract.py` | Task-Family `specialist_decision` im bestehenden `CreateTrainingJobCommand`; Job-Manifest führt `specialist_id`, `contract_version`, `contract_digest`, `dataset_digest`, `holdout_digest`, `benchmark_version`. Scorer im ML-Intern-Eval-Service registriert. |
| GBMF-005 | `agent/services/specialist_candidates.py` | `CandidatePlan` (gleiche Datenbasis, gleiches Budget, ≥2 Basemodelle), `choose_method` (LoRA/QLoRA nach Größe), `compare_candidates`: der kleinste/günstigste Kandidat gewinnt, wenn Qualitäts- und Safety-Schwellen halten. |
| GBMF-006 | `agent/services/specialist_candidates.py` | `ArtifactLineage`: Adapter ist Primärartefakt; Merge/GGUF-Exporte nur nach Evaluation, als `verified=False` bis separat geprüft; eindeutig auf Base Model, Adapter, Dataset, Job rückführbar. |
| GBMF-007 | `agent/services/specialist_benchmark.py` | `ananta.specialist-benchmark-result.v1`: Accuracy, Macro-F1, Precision/Recall je Klasse, Confusion Matrix, False-Allow/False-Deny getrennt, Latenz/Tokens/VRAM/Trainingsbudget, Deltas gegen `untrained_base`, `prompt_gate_stack`, `previous_specialist`. |
| GBMF-008 | `agent/services/specialist_calibration.py` | Brier Score, ECE, Reliability Bins; Abstain-Schwelle auf Validation gewählt, nur auf Holdout geprüft; Escalation-Rate und Fehlerquoten unter/über Schwelle. |
| GBMF-009 | `agent/services/specialist_promotion_gate.py` | `decide_promotion`: reine Funktion über gespeicherte Artefakte (Policy-Digest, Result-Digests). Blockiert bei unvollständiger Provenance, Hard-Minimum-Verletzung, Safety-Regression, schlechter Calibration, fehlender Verbesserung; Speed/Kosten-Verbesserung nur innerhalb der Qualitätstoleranz. |
| GBMF-010 | `agent/services/specialist_retraining_queue.py` | `RetrainingCandidateQueue`: Fehlerklassen (false_allow, false_deny, abstention_error, parser_error, …), nur verifizierte Labels, Priorisierung wiederholter Klassen, Holdout-Kontaminationsschutz, `DatasetVersion` mit Changelog. |
| GBMF-011 | `agent/services/specialist_model_factory.py` | `SpecialistModelFactory.run_cycle`: Split → Admission → ML-Intern-Job je Kandidat (über `SpecialistTrainerPort`) → Vorhersagen (`SpecialistPredictorPort`) → Benchmark + Calibration → Ranking → Promotion-Entscheidung → Registry (`register → training → trained → set_eval_report → approve|reject`), `rollback`. |

## Governance-Regeln (umgesetzt)

* Ein Modell wird nur `approved`, wenn `decide_promotion` es erlaubt; Trainingserfolg
  allein aktiviert nichts (`tests/test_specialist_model_factory_e2e.py`).
* Der Holdout wird vor dem Training eingefroren; Dataset-Version 2 behält denselben
  `holdout_digest`, sonst ist kein Vergleich gegen Version 1 möglich
  (`specialist_previous_requires_frozen_holdout`).
* Confidence ≠ Korrektheit: Calibration wird separat gemessen und kann Promotion blockieren.
* Rollback läuft über die bestehende Registry (`MlInternAdapterRegistryService.rollback`);
  Ziel ist die letzte freigegebene Version desselben Basemodells.
* Der Hub bleibt Control Plane: Trainer-/Predictor-Ports sind die einzigen Ausführungsgrenzen,
  kein Zugriff auf Trainer-CLIs oder Backend-WebUIs.

## Noch offen

* Anbindung der Ports an den realen ML-Intern-Worker-Transport (heute: Ports +
  Mock-Backend im Referenzlauf); der Job-Vertrag ist bereits der produktive.
* Persistenz der Example-Ledger/Dataset-Versionen im Hub-Dataset-Katalog
  (aktuell In-Memory-Objekte mit Digests).
* Erfassung realer Gate-Läufe (Hooks in den bestehenden Gates), Ressourcenmessung
  aus echten Trainings-Events.

## Tests

```bash
cd docker/compose-next
docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm t-infra \
  sh -c "python -m pytest -q tests/test_specialist_model_factory.py tests/test_specialist_model_factory_e2e.py"
```
