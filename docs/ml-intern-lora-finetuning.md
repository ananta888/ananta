# Lokales ML-Intern LoRA/QLoRA-Training

## Zweck und Grenzen

Ananta kann lokale PEFT-Adapter als LoRA oder QLoRA aus kuratierten JSONL-Daten
erzeugen, gegen das unveränderte Basismodell evaluieren, kontrolliert freigeben
und optional für lokale Inferenz routen. Der komplette Produktzugang läuft über
den Hub und den Angular-Bereich `/model-training`.

LoRA ist in Ananta ein Verhaltens- und Format-Layer. Aktuelles Projektwissen
bleibt im RAG-/CodeCompass-Layer. Insbesondere werden keine Chat-, Voice- oder
sonstigen Nutzungsdaten automatisch als Trainingsdaten übernommen.

Nicht Bestandteil dieser Pipeline sind:

- Full-Fine-Tuning aller Modellgewichte,
- automatischer Download von Modellen oder Trainingsdaten,
- Worker-zu-Worker-Orchestrierung,
- automatisches Adapter-Approval,
- ein implizites Merge des Adapters in das Basismodell.

Der historische `ml_intern`-Spike für begrenzte Prompt-Ausführung bleibt davon
getrennt. `/api/sgpt/execute` startet niemals einen Trainingsjob.

## Architektur und Eigentum

```text
Angular / API client
        |
        | authenticated admin API
        v
+--------------------------- Hub ----------------------------+
| admission, tenant binding, idempotency, policy, task queue |
| dataset catalogue, job/attempt/event ledger, audit         |
| worker selection, fencing, approval, runtime routing       |
+-------------------------------+----------------------------+
                                |
                                | authenticated internal v1 contract
                                | exact endpoint allowlist, private address
                                v
+-------------------- isolated training worker --------------+
| verify manifests and hashes; load local model and splits   |
| execute mock / PEFT-TRL / Unsloth; evaluate; checkpoint    |
| emit content-free progress; produce typed artifacts        |
+------------------------------------------------------------+
```

| Verantwortung | Hub | Training-Worker |
|---|---:|---:|
| Benutzer-Authentisierung und Admin-Gate | ja | nein |
| Tenant-/Owner-Bindung | ja | erhält nur materialisierten Auftrag |
| Task-Queue, Admission und maximale Parallelität | ja | nur lokale Ausführungskapazität |
| Idempotenz, Jobstatus, Historie und Audit | ja | attempt-lokaler Status |
| Worker-Auswahl, Retry-Policy und Fencing | ja | prüft Attempt und Fencing-Token |
| Dataset-Upload, Quarantäne, Split und Validierung | ja | prüft Split-Manifest erneut |
| Modell laden, LoRA/QLoRA trainieren und evaluieren | nein | ja |
| Adapter-Approval und Inferenz-Routing | ja | nein |
| Unterdelegation an andere Worker | nein | nein |

Der Hub importiert im Live-Pfad weder Torch noch Unsloth und erhält kein
GPU-Gerät. Ein lokaler Hub-Executor ist nur für `dry_run` beziehungsweise den
expliziten `mock`-Pfad zulässig. Reales Training wird an den isolierten Worker
delegiert.

Die Ports zwischen Hub, Repository, Worker-Transport und Backend-Strategien
schützen DIP und erlauben neue Trainings-Engines ohne Worker-Orchestrierung.
Bewusste bestehende SRP-Schuld bleibt in den großen Kompositionsmodulen für
Training-Routen, Control-Service, HTTP-Worker-Port und Worker-Runtime erhalten;
Persistenz-, Backend- und Registry-Ports begrenzen deren Kopplung, eine weitere
Extraktion ist aber ein separater, schrittweiser Refactoring-Pfad.

## Angular-Nutzerfluss

Der lazy geladene, durch den Angular-`adminGuard` geschützte Einstieg ist
`/model-training`. Die App ermittelt den Hub über die Agent Directory und ruft
niemals eine Worker-URL direkt auf. Der implementierte Ablauf gliedert sich in
vier Tabs:

1. **Datasets:** JSON/JSONL mit Zweck, Lizenz, Privacy-Klasse, Split-Ratio und
   Seed hochladen; Katalog, Train-/Validation-Zähler, Validierungsreport und
   redigierte Record-Vorschau prüfen; bei Bedarf deterministisch neu splitten
   und erneut validieren.
2. **Training starten:** Im vierstufigen Wizard Dataset, Modus, lokales
   Basismodell, Backend, Methode, GPU-Profil und bounded Hyperparameter wählen.
   `live` erfordert ein trainierbares Dataset, passende Hub-Capabilities, eine
   Checkbox und eine begründete Risikobestätigung; `dry_run` bleibt der sichere
   Default.
3. **Jobs & Fortschritt:** Queue/History öffnen, Jobstatus, Metrikverlauf und
   redigierte Events ansehen. Der Monitor bevorzugt SSE und setzt bei Abbruch
   mit Cursor-Polling fort. Ein Cancel benötigt einen Grund und bleibt bis zur
   Worker-Bestätigung beziehungsweise zum gefenceten Abschluss sichtbar.
4. **Adapter & Evaluation:** ZIP oder `adapter_config.json` plus
   `adapter_model.safetensors` importieren, Registry-Status und Hash prüfen,
   Base-vs.-Adapter-Evaluation starten, den Report ansehen und Lifecycle-
   Aktionen sowie Export explizit bestätigen.

Deep-Links können `tab`, `job_id` oder `dataset_id` verwenden, zum Beispiel
`/model-training?tab=jobs&job_id=<OPAQUE_JOB_ID>`.

Die Angular-Oberfläche bindet auch ein separates Validation-Dataset an, wählt
den kanonischen `scorer_name`, löscht unreferenzierte Datasets und bietet
Registry- sowie Runtime-Rollback einschließlich Cache-Unload. Registry-Rollback
ändert den freigegebenen Lifecycle-Eintrag; Runtime-Rollback schaltet zusätzlich
die aktive Inferenzroute um und entlädt die vorherige Adapterinstanz.

## Persistenz und Migration

Die Migration `z4a5b6c7d8e9` folgt additiv auf `y3z4a5b6c7d8` und legt sechs
Hub-eigene Tabellen an:

| Tabelle | Zweck |
|---|---|
| `ml_intern_datasets` | tenantgebundener Dataset-Katalog, Hashes, Split- und Validierungsmetadaten |
| `ml_intern_training_jobs` | kanonischer Jobstatus, Task-Bindung, Fortschritt und Resultatprojektion |
| `ml_intern_training_capacity_leases` | clusterweite, atomare Slots für laufende plus wartende Jobs |
| `ml_intern_training_execution_leases` | ablaufende clusterweite Semaphore für tatsächlich ausgeführte Jobs |
| `ml_intern_training_attempts` | Worker-Claim, Lease, Heartbeat und Fencing-Digest |
| `ml_intern_training_events` | append-only, sequenzierte und deduplizierte Fortschrittsereignisse |

Eindeutigkeits-Constraints verhindern denselben Dataset-Inhalt und dieselbe
Job-Idempotenz innerhalb eines Tenant-/Owner-Scopes sowie doppelte Attempt-
Nummern und Event-Sequenzen. Fremdschlüssel verbinden Job mit Dataset und
Capacity-/Execution-Lease, Attempt und Event mit Job.

Vor dem ersten Start der neuen Funktion:

```bash
python -m alembic upgrade head
```

Ein Downgrade auf `y3z4a5b6c7d8` entfernt diese sechs Tabellen und ist daher nur
für einen geplanten Release-Rollback nach Datenbanksicherung geeignet. Ein
normaler Adapter-Rollback benötigt kein Datenbank-Downgrade.

## Öffentliche Hub-API

Alle Endpunkte haben das Präfix `/api/ml-intern-training`, benötigen einen
authentisierten Administrator und geben Ananta-API-Antworten mit `status` und
`data` zurück. IDs werden an den Tenant und den authentisierten Subject gebunden;
unzulässige fremde IDs werden nicht als Speicherpfade aufgelöst.

Mutierende v2-Endpunkte verlangen den Header:

```http
Idempotency-Key: mindestens-8-zeichen
```

Bei Dataset-Upload, Adapter-Import und asynchroner Jobannahme ist der Key an
Tenant/Subject und Request-Digest gebunden: Ein Replay derselben Nutzlast gibt
das bestehende Ergebnis zurück, dieselbe Key-/Scope-Kombination mit anderer
Nutzlast wird mit `409` abgelehnt. Die übrigen Mutationen validieren den Header
als Pflichtfeld, haben aber zusätzlich ihren jeweiligen Zustandsvertrag; ein
wiederholtes Approval oder eine wiederholte Transition ist daher nicht pauschal
als erfolgreicher Replay zu verstehen.

### Capabilities

| Methode | Pfad | Ergebnis |
|---|---|---|
| `GET` | `/capabilities` | Verfügbarkeit, Modus, Backends, GPU-Profile, lokale Basismodelle und Limits |

Die Angular-App verwendet diesen Endpunkt als Feature-Gate. `available=false`
oder ein `reason_code` darf nicht durch clientseitige Defaults übergangen werden.

### Datasets

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/datasets` | JSON-/JSONL-Datei hochladen, splitten und validieren |
| `GET` | `/datasets` | paginierte Liste; Filter `status`, `format`, `q`, `cursor`, `limit` |
| `GET` | `/datasets/{dataset_id}` | Zähler, Split-Manifest und redigierter Validierungsreport |
| `GET` | `/datasets/{dataset_id}/records` | redigierte Vorschau; `split=train|validation`, `cursor`, `limit` |
| `GET` | `/datasets/{dataset_id}/statistics` | Formatverteilung und Qualitätszähler |
| `POST` | `/datasets/{dataset_id}/split` | deterministischer neuer Split aus `validation_ratio` und `seed` |
| `POST` | `/datasets/{dataset_id}/validate` | erneute Validierung und Secret-Scan |
| `POST` | `/datasets/{dataset_id}/validation-dataset` | separat hochgeladenes Dataset als unveränderliche Validation-Quelle anbinden |
| `DELETE` | `/datasets/{dataset_id}` | nicht referenziertes Dataset aus Katalog und SQL-Projektion löschen |

Upload ist `multipart/form-data`. Das Dateifeld heißt `file`; optionale Felder
sind `name`, `format`, `purpose`, `license`, `privacy`, `validation_ratio`,
`split_seed` und `sha256`.

```bash
curl -X POST http://hub:5000/api/ml-intern-training/datasets \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: dataset-todo-v1-001" \
  -F "file=@train.jsonl;type=application/x-ndjson" \
  -F "name=Todo JSON v1" \
  -F "format=instruction" \
  -F "validation_ratio=0.1" \
  -F "split_seed=42"
```

Der Upload liefert eine opaque `dataset_id`; nachfolgende Requests verwenden
nur diese ID. Absolute Pfade sind kein v2-API-Vertrag.

Unterstützt werden die fachlichen Formate `instruction` und `chat`:

```json
{"instruction":"Erzeuge ein valides Todo-JSON.","output":"{\"track\":\"...\"}"}
```

```json
{"messages":[{"role":"user","content":"Erzeuge ein valides Todo-JSON."},{"role":"assistant","content":"{\"track\":\"...\"}"}]}
```

Eine `.jsonl`-Datei enthält genau ein JSON-Objekt je nichtleerer Zeile. Eine
`.json`-Datei darf eine Liste, ein einzelnes Objekt oder ein Objekt mit
`records`, `examples` beziehungsweise `items` enthalten. Im
Instruction-Format werden `prompt`/`input` und `completion`/`response` auf
`instruction`/`output` normalisiert; Chat und Instruction können kontrolliert
ineinander überführt werden. Nicht unterstützte Records, zu kurze Inhalte und
exakte Duplikate werden gezählt beziehungsweise verworfen, nicht still als
zusätzliche Beispiele erfunden.

Alternativ zum prozentualen Split können zwei zuvor hochgeladene Datasets
explizit als Train-/Validation-Paar verbunden werden. `{dataset_id}` bezeichnet
dabei das Trainings-Dataset; der Body benötigt die ID des separaten
Validation-Datasets:

```bash
curl -X POST \
  http://hub:5000/api/ml-intern-training/datasets/lora-train-001/validation-dataset \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: validation-pair-v1-001" \
  -d '{"validation_dataset_id":"lora-validation-001"}'
```

Beide IDs müssen zum selben authentisierten Tenant-/Owner-Scope gehören und
verschieden sein. Der Hub liest die unveränderlichen Katalogquellen, validiert
beide Seiten einschließlich Secret-Scan, verwirft leere Paare sowie
normalisierte semantische Überschneidungen und schreibt erst danach atomar das
Split-Manifest `external-validation-dataset-v1`. Das Validation-Dataset wird
als referenziert markiert und kann daher nicht unbemerkt gelöscht werden.

Der Split gruppiert normalisierte Duplikate und weist vollständige Gruppen mit
einem Seed stabil Train oder Validation zu. Dadurch erscheint ein semantisch
identischer Record nicht auf beiden Seiten. Beide Partitionen müssen nicht leer
sein. Split-Hashes und Record-Zahlen werden im Manifest festgehalten.

Die Preview ist cursorbasiert, begrenzt Text und Struktur und redigiert unter
anderem Credentials, Token, E-Mail-Adressen und lokale Pfade. Joblisten, Events
und Audit-Logs enthalten niemals Trainingsbeispiele.

Ein Secret-Finding blockiert standardmäßig die Trainingsfreigabe. Ein Override
ist nur über `/validate` mit `allow_sensitive_override=true`, Administratorrolle
und aussagekräftigem `override_reason` möglich und wird auditiert.

### Daten-Governance, Aufbewahrung und Löschung

Die Angular-Maske verlangt `purpose`, `license` und eine Privacy-Klasse
(`private`, `internal` oder `public`); der Hub persistiert diese Angaben als
bounded Metadaten. Sie sind deklarativ: Ananta prüft weder die rechtliche
Wirksamkeit einer Lizenz noch Einwilligung oder sonstige Rechtsgrundlage. Es
gibt derzeit kein technisches Consent-Feld. Der hochladende Administrator muss
daher vor dem Upload dokumentiert haben, dass Training, Evaluation und lokale
Speicherung für alle Records erlaubt sind. Chat-, Voice-, RAG- oder sonstige
Anwendungsdaten werden niemals automatisch in diesen Katalog übernommen.

Validierung scannt Train und Validation auf Secrets sowie begrenzte
PII-Muster; Treffer blockieren standardmäßig. Preview-Inhalte werden server-
und clientseitig begrenzt beziehungsweise redigiert. Diese Scanner sind ein
zusätzliches Gate und keine Garantie, dass ein Dataset frei von personenbezogenen
oder vertraulichen Daten ist.

Es gibt keine automatische TTL oder Retention-Löschung. Nicht referenzierte
Datasets werden mit `DELETE /api/ml-intern-training/datasets/{dataset_id}` und
einem `Idempotency-Key` entfernt; die Löschung wird auditiert. Sobald ein
Dataset an einen Job, eine Evaluation oder als externe Validation-Quelle
gebunden wurde, antwortet der aktuelle Vertrag mit `dataset_referenced` und
`409`; ein Force-Delete-/Unreference-Endpunkt existiert nicht. Auch für
Job-/Event-Historie, registrierte Adapter, Exporte und Worker-Checkpoints gibt
es in dieser API keine allgemeine Purge-Funktion. Betreiber müssen die
Aufbewahrungsdauer deshalb vor Datenaufnahme festlegen und die Storage-/Backup-
Lifecycle-Policy außerhalb dieses API-Vertrags betreiben. Daten mit zwingender
späterer Hard-Delete-Anforderung dürfen bis zu einem passenden Löschworkflow
nicht in einen Trainings- oder Evaluationsjob eingehen.

### Asynchrone Trainingsjobs

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/jobs` | Job annehmen und in der Hub-Taskqueue materialisieren |
| `GET` | `/jobs` | Historie mit `status`, `cursor` und `limit` |
| `GET` | `/jobs/{job_id}` | Detail, Fortschritt, Metriken, Fehler und Resultatreferenzen |
| `GET` | `/jobs/{job_id}/events` | cursorfähige Events mit `after_sequence` und `limit` |
| `POST` | `/jobs/{job_id}/cancel` | queued Job abbrechen oder Cancel an laufenden Attempt weitergeben |

Beispiel für einen echten QLoRA-Lauf:

```json
{
  "dataset_id": "lora-dataset-...",
  "job_type": "train_lora",
  "mode": "live",
  "backend": "peft_trl",
  "base_model": "qwen2.5-coder-7b-local",
  "method": "qlora",
  "gpu_profile": "rtx3080-safe",
  "output_name": "ananta-todo-json-v1",
  "live_confirmed": true,
  "hyperparameters": {
    "seed": 42,
    "max_steps": 100,
    "evaluation_steps": 10,
    "learning_rate": 0.0002,
    "batch_size": 2,
    "gradient_accumulation_steps": 4,
    "lora_rank": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "max_seq_length": 2048,
    "load_in_4bit": true,
    "early_stopping_patience": 3
  }
}
```

Eine neue Annahme antwortet mit `202`, `id`, `task_id`, `poll_url` und
`events_url`. Ein idempotentes Replay antwortet mit dem vorhandenen Job. Der
HTTP-Request wartet nicht auf Modelltraining.

`max_concurrent_jobs` begrenzt die aktiven Hub-Ausführungen;
`max_queued_jobs` begrenzt die zusätzlich persistierte Hub-Warteschlange. Ist
die Summe beider Kontingente belegt, antwortet die Admission mit `429` und
`training_capacity_exhausted`. Ein wartender Job enthält eine persistierte,
einsbasierte `queue_position`; bei Reservierung eines Ausführungs-Slots wird sie
`null`. Positionsänderungen erscheinen zusätzlich als
`queue_position_changed`-Event. Der Scheduler arbeitet Tenant-Round-Robin und
innerhalb eines Tenants nach `created_at` und Job-ID, damit ein Tenant die
Warteschlange nicht dauerhaft monopolisiert. Diese Hub-Grenze ist unabhängig
von `ANANTA_LORA_TRAINING_MAX_QUEUE`, der lokalen Worker-Warteschlange. Der
normalisierte Hub-Default für `max_queued_jobs` ist 32; gültig sind 0 bis 10.000,
während `max_concurrent_jobs` auf 1 bis 16 begrenzt ist.

Der v2-Statusautomat ist:

```text
queued -> claimed -> running -> completed
   |         |          |
   +---------+----------+-> cancel_requested -> cancelled
             +-----------> interrupted -> bounded retry or failed
             +-----------> failed
```

Terminal sind `completed`, `cancelled` und `failed`. Event-Nutzlasten sind auf
Phase, Reason-Codes und numerische Metriken wie Step, Epoch, Loss und Learning
Rate begrenzt. Der Client speichert `next_sequence` und kann nach einem
Verbindungsabbruch ohne doppelte Inhalte weiterpollen.

Der bisherige `dataset_path`-Request bleibt nur als additive Legacy-
Kompatibilität bestehen. Neue Clients müssen den v2-Vertrag mit `dataset_id`
und Idempotency-Key verwenden.

### Adapter, Evaluation, Approval und Export

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/adapters/import` | vorhandenen PEFT-Adapter sicher importieren |
| `GET` | `/adapters` | Registry- und Importstatus anzeigen |
| `POST` | `/evaluations` | Base-vs-Adapter-Evaluation auf einem Dataset anfordern |
| `GET` | `/evaluations/{evaluation_id}` | Metriken und begrenzte Base-/Adapter-Vergleichssamples abrufen |
| `POST` | `/adapters/{adapter_id}/approve` | evaluierten Adapter manuell freigeben |
| `POST` | `/adapters/{adapter_id}/reject` | evaluierten Adapter ablehnen |
| `POST` | `/adapters/{adapter_id}/deprecate` | aktiven oder abgelehnten Adapter stilllegen |
| `POST` | `/adapters/{adapter_id}/rollback` | Registry-Rollback/Deprecation; kein Runtime-Cache-Unload |
| `POST` | `/adapters/{adapter_id}/export` | hashgeprüftes, deterministisches ZIP erstellen |
| `GET` | `/exports/{artifact_id}` | Export herunterladen; SHA-256 steht im Response-Header |

Ein Import akzeptiert entweder ein Feld `bundle` mit ZIP/TAR oder die beiden
Felder `adapter_config` und `adapter_model`. Pflichtangaben sind `adapter_id`
beziehungsweise `name`, `version` und `base_model_id`.

```bash
curl -X POST http://hub:5000/api/ml-intern-training/adapters/import \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: adapter-import-v1-001" \
  -F "adapter_id=ananta-todo-json" \
  -F "version=1" \
  -F "base_model_id=qwen2.5-coder-7b-local" \
  -F "bundle=@adapter.zip;type=application/zip"
```

Archive werden zuerst in einer tenantgebundenen Quarantäne geprüft. Pfad-
Traversal, Symlinks, Hardlinks, Device-Files, Archive-Bombs, übergroße Header
und unbekannte Dateien werden abgelehnt. Gewichte müssen
`adapter_model.safetensors` sein; Pickle-/BIN-Gewichte werden nicht akzeptiert.
Die Base-Model-Bindung aus `adapter_config.json` muss exakt zur Request-Angabe
passen. Erst danach wird der Baum atomar in den Artifact-Bereich verschoben.
Beim Import oder bei der Veröffentlichung eines Trainingsergebnisses berechnet
der Hub zusätzlich `artifact_sha256`: SHA-256 über die kanonisch sortierte Liste
aus Dateiname, Dateigröße und Datei-SHA-256 des validierten Adapterbaums. Dieser
Wert wird unveränderlich im Registry-Eintrag gebunden.

Der Lifecycle lautet:

```text
created -> training -> trained -> evaluated -> approved -> deprecated
                                  \-> rejected -> deprecated
```

Eine Evaluation wird mit `adapter_id`, `dataset_id` und optionalem
`scorer_name` angefordert. Unterstützt sind `generic` (Default), `todo_json` und
der gleichwertige Alias `ananta_todo_json`. `generic` bewertet die erwartete
Ausgabe deterministisch; der Todo-Scorer prüft die aktuelle Ananta-Todo-Struktur
einschließlich Track/Owner, Status-/Prioritäts-/Risiko-Skalen, Milestones,
feingranularen Tasks, Acceptance Criteria, Test Expectations und gültigen
Querverweisen.

```json
{
  "adapter_id": "ananta-todo-json",
  "dataset_id": "lora-validation-ready-001",
  "scorer_name": "ananta_todo_json"
}
```

Die persistierte Evaluation enthält endliche Base-/Adapter-Metriken,
Scorer-Ergebnisse und eine begrenzte Zahl von Vergleichssamples. Der
Evaluation-Detailendpunkt ist deshalb ausschließlich für Administratoren
bestimmt; Samples werden nicht in Joblisten, Events oder Audit-Nutzlasten
kopiert. Ist kein isolierter Evaluation-Worker verfügbar, schlägt die
Anforderung fail-closed mit `training_worker_unavailable` oder
`worker_capability_unavailable` und HTTP `503` fehl.

Approval und Reject verlangen im JSON-Body `confirmed: true` und einen
aussagekräftigen `reason`. Approval ist nur aus `evaluated` möglich und – im
sicheren Default – nur mit vorhandenem Evaluation-Report. Import oder Training
aktivieren einen Adapter niemals automatisch.

Der Export ist erst nach Evaluation erlaubt. Er validiert den Adapterbaum
erneut und vergleicht ihn mit dem gebundenen `artifact_sha256`. Legacy-Einträge
ohne Bindung (`adapter_hash_unbound`) sowie nachträglich veränderte Bäume
(`adapter_hash_mismatch`) werden fail-closed abgelehnt. Das Export-Manifest
enthält die Bindung und alle Datei-Hashes; derselbe Inhalt erzeugt dieselbe
Artifact-ID. Auch der Inferenzpfad berechnet den Baum erneut und akzeptiert ihn
nur bei exakter Übereinstimmung mit dem beim Approval vorhandenen Registry-Hash.

## Sichere Konfiguration im Hub

Training bleibt standardmäßig ausgeschaltet. Das folgende Beispiel ist eine
bewusste Live-Konfiguration; Modellpfad und Snapshot-Hash müssen auf die lokal
bereitgestellte, unveränderliche Modellkopie zeigen.

```json
{
  "ml_intern_training": {
    "enabled": true,
    "mode": "live",
    "backend": "peft_trl",
    "allowed_job_types": [
      "dataset_validate",
      "train_lora",
      "evaluate_lora",
      "register_adapter",
      "export_adapter"
    ],
    "artifact_root": "artifacts/lora",
    "dataset_root": "project-workspaces/lora-training/datasets",
    "dataset_catalog_root": "data/training/lora/catalog",
    "timeout_seconds": 3600,
    "max_dataset_bytes": 104857600,
    "max_adapter_bytes": 2147483648,
    "max_concurrent_jobs": 1,
    "max_queued_jobs": 32,
    "max_preview_records": 100,
    "validation_ratio": 0.1,
    "split_seed": 42,
    "require_dataset_validation": true,
    "require_secret_scan": true,
    "require_eval_before_approval": true,
    "auto_activate_adapter": false,
    "external_network_allowed": false,
    "gpu_profile": "rtx3080-safe",
    "reconciliation": {
      "interval_seconds": 5,
      "heartbeat_timeout_seconds": 120,
      "queued_stale_seconds": 30,
      "cancel_grace_seconds": 30,
      "max_attempts": 3,
      "retry_stale_attempts": true,
      "resume_from_checkpoint": true,
      "batch_limit": 100
    },
    "base_model_catalog": {
      "qwen2.5-coder-7b-local": {
        "relative_path": "qwen2.5-coder-7b/snapshots/local",
        "snapshot_hash": "<64-stelliger-sha256>"
      }
    }
  },
  "lora_runtime": {
    "enabled": false,
    "adapter_registry_path": "artifacts/lora/adapter_registry.json",
    "routing_enabled": false,
    "fallback_to_base_model": true,
    "approved_only": true
  }
}
```

`auto_activate_adapter=true` wird nicht als Freigabemechanismus verwendet.
Runtime-Routing wird erst nach erfolgreichem Approval separat aktiviert.
`external_network_allowed=false` entspricht dem offline gebauten Worker; ein
Modell muss vor Start in das read-only Model-Volume gelegt werden.

## Isolierter Worker-Vertrag

Der interne Vertrag hat die Version `ananta.lora-training.v1` und ist keine
öffentliche Benutzer-API. Sein Base-Pfad ist:

```text
/internal/v1/lora-training
```

Er bietet Submit, Status, Heartbeat, Events, Cancel und hashgebundenen
Artifact-Download. Der Hub sendet nur:

- Job-, Attempt- und Correlation-ID sowie monotonen Fencing-Token,
- sichere relative Workspace-, Modell- und Split-Referenzen,
- SHA-256 und Record-Zahl für Train und Validation,
- eine lokale Model-Catalog-Bindung mit Snapshot-Hash,
- begrenzte Hyperparameter und Deadline.

Der Worker prüft alle Pfade innerhalb seiner Root-Verzeichnisse, lehnt
Symlinks ab und verifiziert Hash und Record-Zahl vor dem Modellladen. Redirects,
öffentliche Zieladressen, nicht exakt allowlistete Worker-URLs und Antworten
oberhalb der Limits werden Hub-seitig abgelehnt.

Der Worker erzeugt mindestens:

| Artefakt | Inhalt |
|---|---|
| `adapter_config.json` | PEFT-LoRA-Konfiguration und Base-Model-Bindung |
| `adapter_model.safetensors` | sichere Adaptergewichte |
| `evaluation.json` | aggregierte Base-/Adapter-Metriken |
| `training_manifest.json` | Dataset-/Model-/Config-Hashes, Software, Hardware und Provenance |

Tokenizer-Dateien und Checkpoints sind optional und bleiben auf den jeweiligen
Job-/Attempt-Bereich begrenzt. Der Hub lädt jedes veröffentlichte Artefakt
bounded herunter, prüft Größe und Hash und validiert den PEFT-Baum erneut, bevor
er ihn in die Registry übernimmt.

## Compose und Betriebsprofile

Das additive Overlay ist `docker/compose-next/compose.lora-training.yml`. Genau
ein Profil wird gewählt. Der interne Bearer muss mindestens 24 Zeichen haben
und zwischen Hub und Worker identisch sein.

### Mock / CI

```bash
export ANANTA_LORA_TRAINING_INTERNAL_TOKEN='replace-with-a-random-32-byte-secret'
export ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON='{"local/model":{"relative_path":"model","snapshot_hash":"<sha256>"}}'
export ANANTA_LORA_TRAINING_MODEL_DIR='/absolute/path/to/local/models'
scripts/run-lora-training-stack.sh lora-training-mock
```

Das Minimal-Image testet den vollständigen asynchronen Vertrag, Split-Prüfung,
Fortschritt, Cancel, Persistenz und Artifact-Transfer ohne ML-Framework. Es ist
kein Nachweis für reale Modellqualität.

### CPU

```bash
export ANANTA_LORA_TRAINING_INTERNAL_TOKEN='replace-with-a-random-32-byte-secret'
export ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON='{"local/model":{"relative_path":"model","snapshot_hash":"<sha256>"}}'
export ANANTA_LORA_TRAINING_MODEL_DIR='/absolute/path/to/local/models'
scripts/run-lora-training-stack.sh lora-training-cpu
```

Das CPU-Profil enthält Torch CPU sowie PEFT/TRL. Für CPU muss
`load_in_4bit=false` beziehungsweise `quantization=none` verwendet werden;
4-/8-bit-QLoRA ist im Backend an ein CUDA-Gerät gebunden. CPU-Läufe eignen sich
für sehr kleine lokale Modelle und End-to-End-Abnahme, nicht für ein realistisches
7B-Training in kurzer Zeit.

### NVIDIA

```bash
export ANANTA_LORA_TRAINING_INTERNAL_TOKEN='replace-with-a-random-32-byte-secret'
export ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON='{"local/model":{"relative_path":"model","snapshot_hash":"<sha256>"}}'
export ANANTA_LORA_TRAINING_MODEL_DIR='/absolute/path/to/local/models'
scripts/run-lora-training-stack.sh lora-training-nvidia
```

Der Wrapper setzt pro Profil konsistent Hub-Modus, Hub-Default-Backend,
zugelassene Worker-Backends, Ressourcenprofil und GPU-Profil. Das Overlay
verlangt diese Bindung sowie Modellkatalog und Modellverzeichnis explizit und
startet daher nicht still mit einem Dry-run-/Mock-Hub vor einem CPU- oder
NVIDIA-Worker.

Das NVIDIA-Image ist auf den CUDA-12.4-Abhängigkeitssatz festgelegt und enthält
PEFT/TRL, bitsandbytes und Unsloth. Docker muss die NVIDIA-Container-Runtime und
mindestens ein freigegebenes GPU-Gerät sehen. Das Profil `rtx3080-safe` startet
konservativ mit 4-bit, Rank 16, Sequenzlänge 2048, Batch 2 und Gradient-
Accumulation 4. Die tatsächliche Kapazität hängt von Modellarchitektur,
Sequenzverteilung und freiem VRAM ab.

Das NVIDIA-Compose-Profil setzt
`ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION=0.90`. Noch vor dem Laden eines
Backend-Moduls validiert jeder isolierte Jobprozess den Wert strikt als finite
Zahl in `(0, 1]` und setzt ihn über den PyTorch-Caching-Allocator für jedes
sichtbare CUDA-Device. Ein ungültiger Wert, fehlendes CUDA oder ein nicht
anwendbarer Allocator beendet den Versuch fail-closed. Die Fraktion ist eine
Allocator-Obergrenze gegen unkontrollierte PyTorch-Belegung, aber keine harte
Docker- oder Hardware-VRAM-Quota: CUDA-Kontext, Fremdbibliotheken und andere
Prozesse können Speicher außerhalb dieses Allocators belegen; auch unterhalb
der Grenze bleibt ein OOM möglich.

Die Hub-Profile sind bewusst konservativ und keine Hardware-Garantie:

| Hub-Profil | Default | Harte Profilgrenzen |
|---|---|---|
| `rtx3080-safe` | 4-bit, Rank 16, Sequenz 2048, Batch 2, Accumulation 4 | Batch höchstens 8, Sequenz höchstens 4096 |
| `generic-safe` | 4-bit, Rank 8, Sequenz 1024, Batch 1, Accumulation 8 | Batch höchstens 4, Sequenz höchstens 2048 |
| `none` | keine Quantisierung, Rank 8, Sequenz 512, Batch 1 | Batch höchstens 2, Sequenz höchstens 512; wird auf CPU geroutet |

Eine stärkere NVIDIA-GPU verwendet ebenfalls das Compose-Profil
`lora-training-nvidia`; es gibt keinen automatischen „größere GPU“-Modus und
keine Multi-GPU-Orchestrierung. Höhere Werte dürfen nur innerhalb der
Vertragsgrenzen und nach einem hardwaregebundenen Smoke-Lauf gesetzt werden.
Bei OOM wird der bestehende Job nicht auf veränderte Hyperparameter umgebogen:
Batch/Sequenz/Rank werden in einer neuen Nutzlast reduziert, Accumulation kann
erhöht werden, und ein neuer Idempotency-Key startet den nachvollziehbaren Lauf.

Alle Profile laufen als UID/GID `10005`, mit read-only Root-Filesystem,
entfernten Linux-Capabilities, `no-new-privileges`, PID-/CPU-/RAM-Limits,
separatem State-Volume und ohne veröffentlichten Host-Port. Das
`lora-training-control`-Netz ist intern; Modell- und Dataset-Mounts sind für den
Worker read-only. Die Hugging-Face-/Transformers-Offline-Variablen verhindern
Netzwerk-Fallbacks.

### Relevante Umgebungsvariablen

Hub-seitig:

| Variable | Bedeutung |
|---|---|
| `ANANTA_LORA_TRAINING_WORKER_URL` | exakter interner Endpoint einschließlich `/internal/v1/lora-training` |
| `ANANTA_LORA_TRAINING_ALLOWED_ENDPOINTS` | kommaseparierte, exakte Allowlist; enthält den konfigurierten Endpoint |
| `ANANTA_LORA_TRAINING_TOKEN` | interner Bearer; alternativ file-managed Token |
| `ANANTA_LORA_TRAINING_TOKEN_FILE` | absoluter, sicherer Token-Dateipfad |
| `ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON` | im Compose-Start erforderlicher Katalog lokaler Modellpfade und Snapshot-Hashes |
| `ANANTA_LORA_TRAINING_DATASET_ROOT` | mit dem Worker konsistente Dataset-Root |
| `ANANTA_LORA_TRAINING_WORKSPACE_ROOT` | Hub-Arbeitsbereich für Jobmaterialisierung |
| `ANANTA_LORA_TRAINING_ARTIFACT_ROOT` | persistenter Hub-Root für verifizierte Adapter und Reports; enthält keine Basismodellgewichte |
| `ANANTA_ML_TRAINING_RECONCILE_INTERVAL_SECONDS` | Hub-only Reconcile-Intervall, begrenzt auf 0,25 bis 300 Sekunden |
| `ANANTA_LORA_INFERENCE_WORKER_URL` | optionaler dedizierter Inferenz-Endpoint einschließlich `/internal/v1/lora-training`; fällt auf `ANANTA_LORA_TRAINING_WORKER_URL` zurück |
| `ANANTA_LORA_INFERENCE_ALLOWED_ENDPOINTS` | exakte Inferenz-Allowlist; fällt auf die Training-Allowlist zurück |
| `ANANTA_LORA_INFERENCE_TOKEN` | mindestens 24 Zeichen langer Inferenz-Bearer; fällt auf das Training-Token zurück |
| `ANANTA_LORA_INFERENCE_TOKEN_FILE` | file-managed Inferenz-Token; fällt auf den Training-Token-Pfad zurück |
| `ANANTA_LORA_INFERENCE_TIMEOUT_SECONDS` | Worker-Request-Deadline; Default 120, zulässig im Hub-Service 1 bis 300 Sekunden |

Worker-seitig:

| Variable | Bedeutung |
|---|---|
| `ANANTA_LORA_TRAINING_TOKEN` | Bearer für interne Requests |
| `ANANTA_LORA_TRAINING_STATE_ROOT` | schreibbarer Status-, Event- und Checkpoint-Root |
| `ANANTA_LORA_TRAINING_WORKSPACE_ROOT` | lesbarer materialisierter Workspace-Root |
| `ANANTA_LORA_TRAINING_DATASET_ROOT` | read-only Dataset-Root |
| `ANANTA_LORA_TRAINING_MODEL_ROOT` | read-only lokaler Modell-Root |
| `ANANTA_LORA_TRAINING_BACKENDS` | `mock`, `peft_trl`, `unsloth` oder eine zugelassene Kombination |
| `ANANTA_LORA_TRAINING_RESOURCE_PROFILE` | `mock`, `cpu` oder `nvidia` |
| `ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION` | nur NVIDIA: finite PyTorch-Caching-Allocator-Fraktion je sichtbarem Device in `(0, 1]`; Compose-Default `0.90`, keine harte VRAM-Quota |
| `ANANTA_LORA_TRAINING_MAX_WORKERS` | maximale parallele Ausführungen |
| `ANANTA_LORA_TRAINING_MAX_QUEUE` | zusätzliche bounded Worker-Warteschlange |
| `ANANTA_LORA_TRAINING_MAX_REQUEST_BYTES` | Limit für interne JSON-Envelopes |
| `ANANTA_LORA_TRAINING_MAX_DATASET_BYTES` | maximale Summe der Split-Dateien |
| `ANANTA_LORA_TRAINING_MAX_DATASET_RECORDS` | maximales Record-Limit |
| `ANANTA_LORA_TRAINING_ISOLATE_PROCESSES` | führt schwere Backends in abbrechbaren Kindprozessen aus |
| `ANANTA_LORA_TRAINING_TERMINATION_GRACE_SECONDS` | Grace Period vor hartem Prozessabbruch |
| `ANANTA_LORA_INFERENCE_MAX_LOADED_ADAPTERS` | Cache-Limit gleichzeitig geladener Adapter; Default 1, Maximum 16 |
| `ANANTA_LORA_INFERENCE_MAX_PROMPT_CHARS` | Prompt-Limit; Default 1.048.576, zulässig 1.024 bis 8.388.608 Zeichen |
| `ANANTA_LORA_INFERENCE_MAX_RESPONSE_CHARS` | Antwort-Limit; Default 4.194.304, zulässig 1.024 bis 16.777.216 Zeichen |
| `ANANTA_LORA_INFERENCE_MAX_ADAPTER_BYTES` | Größenlimit eines materialisierten Adapters; Default 2 GiB, Maximum 64 GiB |

Im Compose-Overlay zeigen Training und Inferenz auf denselben isolierten
Worker-Alias und erhalten denselben `ANANTA_LORA_TRAINING_INTERNAL_TOKEN` als
Container-Credential. Die Inferenzvariablen erlauben eine getrennte direkte
Bereitstellung, lockern aber weder die exakte Endpoint-Allowlist noch die
Private-Address-Prüfung. Sind Inline- und file-managed Token gleichzeitig
gesetzt, müssen sie übereinstimmen; andernfalls startet der Port fail-closed.

`GET /health` ist absichtlich inhaltsarm und zeigt unter anderem
`auth_configured`, `runtime_configured`, Backend-Verfügbarkeit und Kapazität. Ein
Worker mit fehlender Auth, fehlenden Roots, fehlenden Packages oder einem
NVIDIA-Profil ohne sichtbares Device bleibt `degraded` und nimmt keine Jobs an.

## Approval, Aktivierung und Runtime-Management

Nach erfolgreicher Evaluation wird ein Adapter manuell freigegeben. Erst danach
kann `lora_runtime.enabled=true` und `routing_enabled=true` gesetzt werden.
`approved_only` bleibt `true`; der Router wählt nur einen passenden approved
Adapter für Basismodell und optionalen Task-Kind.

Die operative Management-API hat ein eigenes Präfix und ist nicht mit dem
Registry-Lifecycle-Endpunkt der Training-API zu verwechseln:

| Methode | Vollständiger Pfad | Wirkung |
|---|---|---|
| `GET` | `/api/ml-intern-lora-runtime/capabilities` | Inferenz- und Cache-Management-Capabilities des isolierten Workers |
| `POST` | `/api/ml-intern-lora-runtime/adapters/{adapter_id}/unload` | konkrete Adapter-Version aus dem Worker-Cache entfernen; Registry-Status bleibt unverändert |
| `POST` | `/api/ml-intern-lora-runtime/adapters/{adapter_id}/rollback` | approved Adapter deprecaten, Cache-Unload anfordern und sicheren Vorgänger bestimmen |

Alle drei Endpunkte verlangen Authentisierung und Administratorrolle. Beide
mutierenden Requests akzeptieren `confirmed`, `reason` und optional
`expected_version`; andere Felder werden abgelehnt. `confirmed` muss `true`
sein, `reason` muss 10 bis 512 Zeichen lang sein und `expected_version` bindet
die Mutation als positive Ganzzahl an den zuletzt gelesenen Registry-Stand.

```bash
curl -X POST \
  http://hub:5000/api/ml-intern-lora-runtime/adapters/ananta-todo-json/rollback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirmed":true,"reason":"Regression im produktiven Todo-Format","expected_version":7}'
```

Runtime-Rollback erfolgt ohne Merge oder Änderung von Basismodellgewichten:

1. `POST /api/ml-intern-lora-runtime/adapters/{id}/rollback` mit
   `confirmed=true` und Grund ausführen.
2. Prüfen, dass der Adapter `deprecated` ist.
3. `cache_unload` prüfen; eine Worker-Störung ändert die bereits verbindliche
   Deprecation nicht, wird aber als degradiertes Unload-Ergebnis sichtbar.
4. `rollback_target` prüfen: der neueste bereits approved Adapter desselben
   Basismodells oder explizit `base_model_only`. Ein unapproved Adapter wird nie
   automatisch hochgestuft.
5. Optional `lora_runtime.routing_enabled=false` als globalen Kill-Switch setzen.
6. Eine Testinferenz ausführen und Routing-Provenance beziehungsweise Audit prüfen.

Bei `fallback_to_base_model=true` verwendet der Runtime-Pfad nach Deprecation
das Basismodell oder den normalen Backend-Pfad. Bei
`fallback_to_base_model=false` wird ein Adapterfehler sichtbar zurückgegeben;
dieser Modus eignet sich nur, wenn stiller Fallback fachlich unzulässig ist.

## Restart, Recovery und Retry

Recovery bleibt Hub-gesteuert. Ein Worker darf sich nach einem Neustart nicht
selbst einen neuen Auftrag geben.

Der Worker persistiert Request, Status, Events und Checkpoint-Provenance im
State-Volume. War ein Attempt beim Worker-Neustart aktiv, wird er dort als
`failed` mit `worker_restarted` und `retryable=true` markiert. Das ist keine
automatische Resume-Zusage.

Der Hub-Dienst `MlInternTrainingReconciliationService.run_once()` wird bounded
und ausschließlich Hub-seitig durch den Background-Reconciler aufgerufen. Ein
Durchlauf:

1. begrenzt die Anzahl betrachteter nichtterminaler Jobs,
2. erkennt abgelaufene Attempt-Leases beziehungsweise fehlende Heartbeats,
3. markiert den bisherigen Attempt fenced und den Job `interrupted`,
4. entscheidet anhand stabiler Reason-Codes und einer begrenzten Retry-Policy,
   ob ein neuer Attempt zulässig ist,
5. erhöht Attempt-Nummer und Fencing-Token vor jeder Neuübernahme,
6. verwirft verspätete Events oder Resultate des alten Attempts,
7. schreibt jede Entscheidung als content-freies Event und Audit.

Die Policy steht unter `ml_intern_training.reconciliation`. Die sicheren
Defaults sind 120 Sekunden Heartbeat-Timeout, 30 Sekunden für stale queued Jobs
und Cancel-Grace, höchstens drei Attempts sowie maximal 100 Jobs je Durchlauf.
`retry_stale_attempts=false` schaltet Neuübernahmen ab; `resume_from_checkpoint`
erlaubt lediglich die nachfolgende Hash-/Provenance-Prüfung und umgeht sie nicht.
Das Thread-Intervall ist standardmäßig fünf Sekunden und kann mit
`ANANTA_ML_TRAINING_RECONCILE_INTERVAL_SECONDS` bounded überschrieben werden.

Ein Checkpoint darf nur verwendet werden, wenn Job, Source-Attempt,
Base-Model-Hash, Dataset-Hash, Config-Hash und Checkpoint-Hash exakt zur
hashgebundenen Bindung passen. Andernfalls startet der Operator einen neuen Lauf
oder der Job endet fail-closed; niemals wird ein „ähnlicher“ Checkpoint übernommen.

### Operator-Playbook

| Symptom / Reason-Code | Sichere Reaktion |
|---|---|
| `training_worker_unavailable` / `worker_degraded` | Health, internes Netz, Allowlist, Bearer, Mounts und Backend-Packages prüfen; danach bounded Retry zulassen |
| `worker_restarted` / stale lease | Reconciler laufen lassen; alten Attempt nicht manuell reaktivieren |
| `out_of_memory` | Batch und Sequenzlänge reduzieren, Gradient-Accumulation erhöhen, gegebenenfalls Rank reduzieren; neue Nutzlast mit neuem Idempotency-Key |
| `dataset_hash_mismatch` / `dataset_source_changed` | Dataset neu importieren oder splitten; bestehenden Job nicht auf veränderte Daten umbiegen |
| `checkpoint_binding_mismatch` | Checkpoint verwerfen und mit unveränderten Inputs neu starten |
| `artifact_hash_mismatch` / `adapter_artifact_invalid` | Artifact nicht registrieren; Worker-State und Storage prüfen, dann vollständig neu erzeugen |
| `queue_full` / `training_capacity_exhausted` | laufende Jobs abwarten; Parallelitätslimits nicht spontan umgehen |
| `cancel_requested` bleibt stehen | Worker-Health und Reconciler prüfen; nach Grace Period wird nur der isolierte Kindprozess beendet |

Nach Hub-Neustart bleiben Jobs und Eventsequenzen in der Datenbank erhalten. Die
Angular-App setzt mit dem letzten `next_sequence` fort. Nach Worker-Neustart
bleibt der State im Volume; eine Neuübernahme erfolgt ausschließlich über den
Hub-Reconciler und einen höheren Fence.

## Bekannte Betriebsgrenzen

- Ein grünes Mock-/CPU-Gate belegt Control Plane, Verträge und Artefaktfluss,
  nicht CUDA, reale VRAM-Eignung oder Modellqualität.
- Modelle werden niemals automatisch geladen. Fehlende lokale Snapshots,
  ungültige Snapshot-Hashes oder fehlende PEFT/CUDA-Abhängigkeiten bleiben
  fail-closed und müssen im Worker-Image beziehungsweise read-only Model-Volume
  behoben werden.
- Adapter werden nicht in Basismodellgewichte gemerged. Approval, Runtime-
  Routing und Rollback bleiben separate Hub-Entscheidungen.
- Automatischer zeitgesteuerter Retention/Purge ist nicht enthalten; Dataset-
  Löschung und Runtime-/Registry-Rollback bleiben explizite Admin-Aktionen.

## Abnahme

Vor Aktivierung eines realen Profils mindestens ausführen:

```bash
python -m alembic upgrade head
pytest -q tests/test_ml_intern_training_migration.py
pytest -q tests/test_ml_intern_training_contract_v2.py \
  tests/test_ml_intern_training_repository.py \
  tests/test_ml_intern_training_control_service.py
pytest -q tests/worker/test_lora_training_app.py \
  tests/worker/test_lora_training_runtime.py \
  tests/worker/test_lora_training_deployment.py
scripts/run-lora-training-stack.sh lora-training-mock config --quiet
```

Das reproduzierbare, standardmäßig netzwerk- und GPU-freie Acceptance-Gate
bündelt die Mock-/Control-Plane-Tests und schreibt einen redigierten Report nach
`artifacts/lora-training-control-center-gate.json`:

```bash
python scripts/run_lora_training_smoke.py
```

Ohne lokales Modell, NVIDIA-Device, CUDA-Runtime oder vollständige
PEFT/TRL-Abhängigkeiten wird der NVIDIA-Teil ehrlich als `not_run` mit stabilem
`reason_code` protokolliert. Der Default-Lauf darf in diesem Fall erfolgreich
enden, wenn das Mock-Gate bestanden ist; `nvidia_live_proof` bleibt dennoch
`false`. `not_run` ist ausdrücklich kein Live-Training-Nachweis.

Für eine NVIDIA-Freigabe muss ein kleines, bereits lokal vorhandenes
Modellverzeichnis angegeben und das Hardware-Gate verpflichtend gemacht werden:

```bash
python scripts/run_lora_training_smoke.py \
  --nvidia-model /absolute/path/to/local/model \
  --require-nvidia
```

Alternativ liest das Script den Modellpfad aus
`ANANTA_LORA_TRAINING_SMOKE_MODEL_DIR`. Mit `--require-nvidia` führen sowohl
`not_run` als auch `failed` zu einem fehlgeschlagenen Gate; nur `passed` setzt
`nvidia_live_proof=true`. Der Live-Smoke führt genau einen PEFT/QLoRA-Schritt
auf synthetischen Train-/Validation-Records aus und belegt die erzeugten
Safetensors-, Evaluations- und Manifest-Artefakte mit Hashes. Er bewertet keine
Produktionsmodellqualität.
