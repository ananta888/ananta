# Lokales LoRA-/QLoRA-Training

Diese Betriebsanleitung beschreibt den produktionsnahen, lokalen Ablauf vom
Dataset bis zur freigegebenen Adapter-Inferenz. Die ausführliche API- und
Architekturreferenz steht in
[`../ml-intern-lora-finetuning.md`](../ml-intern-lora-finetuning.md).

## Sicherheits- und Architekturgrenze

Der Hub bleibt Control Plane und alleiniger Eigentümer von Taskqueue,
Admission, Workerwahl, Lease/Fencing, Evaluation, Approval und Audit. Der
Training-Worker führt nur einen vollständig materialisierten Auftrag aus. Er
kennt weder das Hub-Repository noch Approval- oder Routingregeln und kann keine
anderen Worker adressieren. Angular spricht ausschließlich die Hub-API an.

Worker laufen non-root mit read-only Root-Dateisystem, `cap_drop: ALL`,
`no-new-privileges`, PID-/CPU-/RAM-Limits, internem Netz ohne Host-Port und
einem eigenen Status-/Checkpoint-Volume. Modelle und Datasets sind read-only
gemountet; Offline-Modus ist der Default. Jede interne Worker-Route,
einschließlich `/health`, benötigt den mindestens 24 Zeichen langen Bearer.

## Bedienablauf

1. Als Admin **Modelltraining** öffnen und ein JSON-/JSONL-Dataset importieren.
   Zweck, Lizenz, Privacy und Einwilligungsgrundlage dokumentieren.
2. Deterministischen Split mit Seed und Ratio erstellen oder ein separat
   importiertes Validation-Dataset binden. Dieselbe normalisierte
   Duplikatgruppe darf nie in beiden Partitionen vorkommen.
3. Validierung ausführen. Secret-/PII-Fund, beschädigte Records, leerer Split,
   Hash-/Count-Abweichung oder semantischer Overlap blockieren Live-Training
   fail-closed. Ein sensibler Override benötigt Admin, Grund und Audit. Den
   aktuell gespeicherten, redigierten Report anschließend über
   `GET /api/ml-intern-training/datasets/{dataset_id}/validation-report`
   prüfen; vor einer Validierung antwortet dieser Endpoint mit `404` und
   `validation_report_not_found`.
4. Im Wizard Dataset-ID, lokales Basismodell, LoRA/QLoRA, Backend und Profil
   wählen. Dry-run bleibt Default; Live verlangt Bestätigung und Begründung.
5. Der Hub antwortet sofort mit Job-/Task-ID. Jobliste und Detail zeigen Queue,
   Phase, monotone Schritte, Loss, Events, Checkpoints und Cancel-Zustand.
6. Nach dem Training `evaluate_lora` ausführen. Base und Adapter verwenden
   exakt dasselbe Validation-Manifest, Seed und dieselben Generationseinstellungen.
7. Nur ein nicht regressiver, passender Eval-Report kann durch einen Admin mit
   Grund freigegeben werden. Importierte Adapter starten ebenfalls nie als
   `approved`.
8. Ein hashverifiziertes Bundle kann exportiert werden. Deprecation und
   Rollback wählen ausschließlich einen früher bereits freigegebenen Adapter
   oder `base_model_only`; Modellgewichte werden nicht in den Hub geladen oder
   automatisch gemergt.

## Dataset- und Datenschutzregeln

Unterstützt werden begrenzte Instruction-, Chat- oder Prompt/Completion-
Records in UTF-8 JSON/JSONL. API und UI verwenden opaque Dataset-IDs, niemals
vom Client gewählte Serverpfade. Preview ist paginiert und textbegrenzt.
Trainingsinhalte, Tokens, interne URLs und absolute Pfade erscheinen weder in
Joblisten noch Events oder Gate-Reports.

Datasets und Adapter werden nicht automatisch extern übertragen und nicht für
anderes Training wiederverwendet. Betreiber müssen Lizenz, Consent,
Aufbewahrungsfrist und Löschgrundlage festlegen. Unreferenzierte Datasets
können gelöscht werden; referenzierte Daten bleiben bis zur kontrollierten
Auflösung ihrer Job-/Splitbindung gesperrt. Runtime-Artefakte, Logs und lokale
Gate-Reports sind nach der betrieblichen Retention-Policy zu löschen.

## Profile und konservative Grenzen

| Profil | Einsatz | Konservative Startwerte |
|---|---|---|
| `lora-training-mock` | CI, Vertrag, UI/E2E | keine ML-Gewichte, 1 Worker |
| `lora-training-cpu` | kleiner PEFT-Lauf | Batch 1, Seq 512, Rank 8, keine Quantisierung |
| `lora-training-nvidia` / RTX 3080 | QLoRA | 4-bit, Batch 1–2, Seq 1024–2048, Rank 8–16, Accumulation 4–8 |
| stärkere einzelne NVIDIA-GPU | explizit getestet | Werte nur schrittweise nach Live-Smoke erhöhen |

Die Grenzen sind keine Hardwaregarantie. Multi-GPU und automatisches
Downscaling sind bewusst nicht enthalten. Bei OOM einen neuen Job mit neuem
Idempotency-Key, kleinerem Batch/Sequence/Rank und gegebenenfalls höherer
Gradient-Accumulation starten. Ein alter Job wird nie still mit anderen
Hyperparametern fortgeführt.

Das NVIDIA-Profil begrenzt den PyTorch-Caching-Allocator jedes isolierten
Jobprozesses standardmäßig mit
`ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION=0.90`. Zulässig sind ausschließlich
finite Werte in `(0, 1]`; Fehlkonfiguration, fehlendes CUDA oder ein nicht
anwendbarer Allocator brechen den Versuch ab. Diese Grenze ist keine harte
VRAM-Quota: CUDA-Kontext, Fremdbibliotheken und andere Prozesse liegen teilweise
außerhalb des PyTorch-Allocators, und ein OOM bleibt auch unterhalb der Fraktion
möglich.

## Start und Gates

Genau ein Workerprofil aktivieren:

```bash
set -euo pipefail

export ANANTA_LORA_TRAINING_INTERNAL_TOKEN='replace-with-at-least-24-random-characters'
export ANANTA_LORA_TRAINING_MODEL_DIR='/absolute/path/to/local/models'
export MODEL_ID='local/model'
export MODEL_RELATIVE_PATH='model'
MODEL_SNAPSHOT_HASH="$(
  python3 -c 'from pathlib import Path; import sys; from scripts.run_lora_training_smoke import _tree_sha256; print(_tree_sha256(Path(sys.argv[1])))' \
    "${ANANTA_LORA_TRAINING_MODEL_DIR}/${MODEL_RELATIVE_PATH}"
)"
export MODEL_SNAPSHOT_HASH
ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON="$(
  python3 -c 'import json, os; print(json.dumps({os.environ["MODEL_ID"]: {"relative_path": os.environ["MODEL_RELATIVE_PATH"], "snapshot_hash": os.environ["MODEL_SNAPSHOT_HASH"]}}, separators=(",", ":")))'
)"
export ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON

scripts/run-lora-training-stack.sh lora-training-mock
# oder ein echtes, explizit bestätigungspflichtiges Profil:
scripts/run-lora-training-stack.sh lora-training-cpu
scripts/run-lora-training-stack.sh lora-training-nvidia
```

Der Wrapper ist der unterstützte Startvertrag. Er bindet Hub-Modus, Default-
Backend, Worker-Capabilities, Ressourcen- und GPU-Profil atomar an das gewählte
Compose-Profil. Das Overlay verlangt diese Variablen absichtlich; ein direkter
CPU-/NVIDIA-Start mit Dry-run-/Mock-Hub bricht bereits beim Rendern ab. Über
`ANANTA_LORA_TRAINING_BASE_COMPOSE` kann statt des Full-Stacks beispielsweise
der Quickstart-Stack gewählt werden.

Der Hash-Befehl muss aus dem Repository-Root ausgeführt werden und verwendet
denselben pfad- und inhaltsgebundenen SHA-256-Baumvertrag wie der Worker. Er
bricht bei fehlenden/leeren Bäumen, Symlinks oder speziellen Dateieinträgen ab.
`MODEL_RELATIVE_PATH` ist immer relativ zum read-only
`ANANTA_LORA_TRAINING_MODEL_DIR`; ein absoluter Modellpfad gehört nicht in den
Katalog.

Ein Live-Job braucht zusätzlich zur aktivierten Live-Konfiguration eine
explizite Bestätigung und einen aussagekräftigen, 8 bis 500 Zeichen langen
Grund. Beispiel nach Upload, Split und erfolgreicher Validierung:

```bash
curl -X POST http://hub:5000/api/ml-intern-training/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: lora-live-run-20260716-001' \
  -d '{
    "dataset_id":"lora-dataset-123",
    "job_type":"train_lora",
    "mode":"live",
    "backend":"peft_trl",
    "base_model":"local/model",
    "method":"qlora",
    "gpu_profile":"rtx3080-safe",
    "output_name":"ananta-local-adapter-v1",
    "live_confirmed":true,
    "risk_reason":"Freigegebener lokaler QLoRA-Lauf auf validiertem Dataset",
    "hyperparameters":{"max_steps":100,"batch_size":1,"max_seq_length":1024,"lora_rank":8,"load_in_4bit":true}
  }'
```

Den aktuell gespeicherten Validation-Report kann der Operator separat abrufen:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://hub:5000/api/ml-intern-training/datasets/lora-dataset-123/validation-report
```

`risk_reason` wird nicht als Klartext in Gate-Reports übernommen; der Hub
auditiert dafür einen SHA-256-Nachweis.

Runtime-`rollback` unterstützt mit `expected_version` einen optimistischen
Compare-and-Swap gegen die zuletzt gelesene Registry-Version. `unload`
akzeptiert das optionale Feld nur als kompatible Request-Form, verändert aber
keinen Registry-Status und bietet dafür keine CAS-Garantie. Ein Unload-Ergebnis
muss deshalb über Capabilities/Cache-Zustand verifiziert werden.

Das GPU-/netzfreie Release-Gate und der optionale NVIDIA-Nachweis werden über
denselben stabilen Entry Point ausgeführt:

```bash
scripts/run-lora-training-e2e.sh
ANANTA_LORA_TRAINING_SMOKE_MODEL_DIR=/absolute/local/model \
  scripts/run-lora-training-e2e.sh --require-nvidia
```

Der Report unter `artifacts/lora-training-control-center-gate.json` enthält
immer den reproduzierbaren Befehl und den Hash der CPU-/Mock-Testsuite, aber
keine Records oder Credentials. Nach einem tatsächlich ausgeführten
NVIDIA-Lauf enthält dessen Teilreport zusätzlich Image-/Buildinput-, Library-,
GPU-, Modell-, Dataset- und Config-Hashes. Fehlende GPU, CUDA-Laufzeit,
Dependencies oder lokales Modell ergeben stattdessen `not_run` mit stabilem
Reason-Code; dies ist niemals ein bestandener NVIDIA-Live-Nachweis.
`--require-nvidia` macht den Skip zum fehlschlagenden Gate.

## Störungsbehebung

| Reason/Beobachtung | Prüfung und Recovery |
|---|---|
| `training_worker_unavailable` / Runtime unavailable | Profil, internen DNS-Alias, exakte Endpoint-Allowlist, Bearer und authentifizierten Healthcheck prüfen. |
| `model_missing` / Hash mismatch | lokalen read-only Mount und Catalog-Snapshot-Hash neu verifizieren; nie Netzwerkdownload als impliziten Fallback zulassen. |
| `dependency_unavailable` | zum CPU/NVIDIA-Image passende gelockte Requirements neu bauen; nicht zur Laufzeit installieren. |
| `out_of_memory` | neuen Job mit kleinerem Batch, Sequence und Rank starten; Checkpoint nur bei exakt gleicher Bindung verwenden. |
| stale/interrupted Job | Hub-Reconcile und Attempt-Lease prüfen; der Hub fenced den alten Attempt und verwirft späte Ergebnisse. |
| Cancel bleibt angefragt | Grace Period abwarten; danach wird nur die Job-Prozessgruppe TERM/KILL beendet und `cancel_mode=forced` gemeldet. |
| Dataset-/Artifact-Hash mismatch | Quelle quarantänen, erneut importieren und niemals den erwarteten Hash nachträglich überschreiben. |
| Approval blockiert | abgeschlossenen tenantgebundenen Eval-Job, Basismodell-/Adapterbindung und Mindestscore prüfen. |

Bekannte Grenzen: Hardware-Training ist nur auf tatsächlich vorhandener lokaler
Hardware beweisbar; Modelle werden nicht heruntergeladen; es gibt kein
Worker-zu-Worker-Routing, kein automatisches Adapter-Merge und keine
automatische Freigabe.
