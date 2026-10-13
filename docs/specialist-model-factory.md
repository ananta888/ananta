# Gate + Benchmark Model Factory (Specialist Decision Models)

Track: `todos/active/todo.gate-benchmark-specialized-model-factory.json`

Ziel ist ein geschlossener Post-Training-Kreislauf aus deterministischen Gates,
Benchmark-Oracles und der vorhandenen ML-Intern-LoRA/QLoRA-Infrastruktur.
Der lokale Referenzlauf prüft die Factory mit Trainer-/Predictor-Doubles und
realer Adapter-Registry. Die produktive Zusammensetzung der Ausführungsports
ist noch offen; das folgende Diagramm beschreibt den Zielablauf:

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
  Die Factory verweigert auch einen ersetzten Holdout, abweichende Benchmark-
  oder Spezialisten-Verträge der Vorgängerversion sowie unpassende Kandidatenpläne
  vor dem ersten Trainingsaufruf. Ein fehlender Vergleich darf nicht stillschweigend
  zu einer Erst-Promotion werden.
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
* Hub-seitige Zuordnung der vollständigen Vorhersagen zum eingefrorenen Holdout:
  Das neue Worker-Artefakt enthält alle Fälle, der Statusbericht bleibt eine
  Vorschau. Der Factory-Consumer muss die gehashten, im zugelassenen Job/Attempt
  gespeicherten Bytes lesen und jede Zeile gegen die tatsächlich zugelassene
  Dataset-Partition prüfen. Die Existenz des Artefakts allein reicht nicht.
* Kandidaten-Evaluation vor Promotion über den zugelassenen Evaluationsjob.
  Die normale LoRA-Inferenz verlangt weiterhin einen freigegebenen Adapter;
  eine Factory-Anbindung darf diese Grenze nicht lockern.

## Lokaler Worker-Fortschritt vom 2026-10-10

`specialist_decision` ist nun ein zugelassener Scorer im echten Worker-Vertrag.
Hub und Worker verwenden dieselbe reine Implementierung in
`ananta_contracts/lora_evaluation.py`; der Worker importiert dafür keine
Hub-Services (SRP/DIP). Die Prüfung verweigert offene oder mehrdeutige JSON-
Objekte und ungültige Konfidenzwerte, einschließlich Boolean, Null, NaN und
Unendlich. Mit einem erwarteten Output zählt das Label; ein korrekt geformtes,
aber falsches Label erhält null Punkte. Die Prüfung ohne konkreten
Spezialisten-Vertrag bestätigt nur die Form, nicht die Zugehörigkeit eines
Labels zum erlaubten Satz oder die Release-Eignung.

Der Runtime-Test startet einen separaten Worker-Prozess, verwendet synthetische
Mock-Modell-Ausgaben und liest den gehashten Evaluationsbericht zurück.
126 Tests über Factory, Scorer, Worker-Verträge, Runtime und Hub-Transportport
bestanden lokal in 5,47 Sekunden. Dies ist eine technische Beobachtung;
kein echtes Modelltraining, Container-Deployment oder Produktions-Release wurde
damit nachgewiesen. GBMF-011 bleibt `partial`.

## Vollständiges Vorhersage-Artefakt

Spezialisten-Evaluationen erzeugen nun zusätzlich
`specialist_predictions.jsonl`. Mock- und PEFT-Evaluator führen jede Zeile
der verifizierten Partition aus. Ein `max_samples` unterhalb der Anzahl der
Datensätze wird vor der Ausführung verweigert. Für die übrigen Scorer bleiben
die bisherigen Stichprobengrenzen erhalten.

Die PEFT-Ausgaben werden als Iterator direkt in das Artefakt geschrieben.
`worker/training/specialist_predictions.py` verantwortet ausschließlich
Abdeckungsprüfung und begrenzte Persistenz (SRP); Modellladen und Generierung
bleiben im Evaluator. Das Artefakt ist auf 64 MiB begrenzt. Fehlende, doppelte,
ungeordnete oder zusätzliche Fälle sowie Abbruch und Quotenüberschreitung
verhindern einen erfolgreichen Abschluss und entfernen die angefangene Datei.
Eine vorhandene Datei wird nicht überschrieben. Die Zeilen-ID ist der Hash
des kanonischen Dataset-Datensatzes, keine `SRC_*`-/`RUN_*`-Evidence-Identität.

Statusmetriken enthalten weiterhin höchstens 20 Vorschaufälle, aber Anzahl und
Gewinnerzähler umfassen alle Fälle. Das bestehende Worker-Artefaktmanifest und
der Hub-Transport prüfen Hash und Größe in der zugelassenen Job/Attempt-Bindung.
Der Hub verweigert den erfolgreichen Spezialisten-Job, wenn das vollständige
Vorhersage-Artefakt fehlt. Die inhaltliche Zuordnung im Factory-Consumer bleibt
offen; diese Transportprüfung ersetzt weder Holdout-Admission noch Promotion.

139 lokale Tests bestanden in 5,89 Sekunden. Darunter: vollständige Ausgabe
von 25 Fällen im separaten Mock-Worker-Prozess, die echte PEFT-Generierungsschleife
mit synthetischen Modell-/Tokenizer-Doubles, unveränderte generische
Stichprobengrenze, negative Abdeckungs-/Quoten-/Abbruchprüfungen und gehashte
Hub-Artefaktübernahme. Es fand kein echtes PEFT-/GPU-Training statt.

## Hub-Prüfung der Vorhersage-Bytes

`agent/services/specialist_prediction_artifact.py` liest das vollständige
Artefakt gegen die vom Hub vorgegebene Dataset-Partition. Vor der Rückgabe
müssen SHA-256, Byteanzahl, exakte Zeilenanzahl, Reihenfolge, kanonische
Datensatz-Hashes und erwartete Ausgaben übereinstimmen. Doppelte JSON-Felder,
Zusatzfelder, fehlende Fälle und nachgestellte Bytes werden verweigert.
Byte- und Zeilengrenzen halten die Verarbeitung begrenzt.

Der Parser gibt ausschließlich rohe Base-/Adapter-Ausgaben zurück. Worker-
Scores und Gewinnerangaben werden nicht zur Benchmark-Berechnung verwendet.
Auch falsche Modellausgaben bleiben erhalten, damit der Hub ihre Fehlerquote
selbst berechnet. Der Parser verantwortet nur Byte-/Datensatzbindung (SRP);
Tenant-, Job-/Attempt-Autorisierung und unveränderliche Artefaktaufnahme bleiben
bei den bestehenden Hub-Diensten. Eine produktive Factory-Komposition muss
diese Dienste mit dem Parser verbinden und anschließend den konkreten
Spezialisten-Vertrag und das eingefrorene Holdout anwenden.

129 lokale Tests bestanden in 5,60 Sekunden. Nach Anschluss des Parsers an
den Test des separaten Mock-Worker-Prozesses bestanden die betroffenen
39 Parser-/Runtime-Tests in 4,18 Sekunden. Das sind überlappende Testläufe,
synthetische technische Beobachtungen und keine Produktions-Release-Evidence.
GBMF-011 bleibt `partial`: Die produktiven Trainer-/Predictor-Ports und ihre
Job-/Dataset-Komposition sind weiterhin offen.

## Dataset-Bindung von Zulassung bis Delegation

Die produktive `MlInternTrainingControlService`-Zulassung schreibt für
`specialist_decision` eine Hub-interne, pfadfreie Dataset-Bindung in den Job:
Katalog-ID und Version, Dataset-Hash, Train-/Validation-Hashes und Zeilenanzahlen.
Sie stammen aus dem tenantgebundenen Katalog; der öffentliche Job-Vertrag
erlaubt dem Aufrufer dieses interne Feld nicht. Unverifizierte oder fehlende
Partitionshashes verhindern die Zulassung eines Spezialisten-Jobs.

Vor der Ausführung muss der erneut geladene Katalogeintrag exakt zur
gespeicherten Bindung passen. Der echte Hub-HTTP-Worker-Port prüft außerdem
die tatsächlich zu übertragenden Partitionshashes und Zeilenanzahlen vor dem
Submit. Ein Katalogwechsel oder ersetzte Dateien dürfen keinen Vergleich auf
einer anderen Datenbasis erzeugen. Generische Trainingsjobs behalten ihren
bisherigen Ablauf. Frühere Spezialisten-Jobs ohne diese Bindung werden
verweigert und müssen neu zugelassen werden; es gibt keine nachträgliche
Bindung während der Ausführung.

Die reine Projektions-/Vergleichslogik liegt in
`agent/services/specialist_dataset_binding.py` (SRP). Die vorhandenen Hub-
Dienste behalten Zulassung, Scheduling, Fencing und Worker-Delegation.
Die bestehende Mischung dieser Abläufe im Execution-Mixin bleibt als
SRP-Schuld erhalten; diese Änderung fügt dort keine weitere Ausführungsrolle
oder unabhängige Queue hinzu.

176 lokale Tests bestanden in 15,84 Sekunden. Der SQL-Test lässt einen Job
über den echten Hub-Service zu, ändert danach die Dataset-Version und weist
nach, dass keine Ausführung stattfindet. Der Transporttest ersetzt die
Partitionsbytes und weist nach, dass kein Worker-Submit erfolgt. Die anderen
Tests decken Factory, Parser, Scorer, Verträge und isolierte Mock-Worker ab.
Dies ist weiterhin synthetische technische Beobachtung. Produktive Factory-
Portkomposition und der vollständige reale Modellzyklus bleiben offen.

## Tenantgebundener Hub-Lesedienst

`SpecialistJobPredictionService` verbindet den Parser mit dem produktiven
Training-Repository. Er verlangt einen abgeschlossenen Spezialisten-
Evaluationsjob und dessen abgeschlossenen, aktiven Versuch für denselben
Tenant und Owner. Dataset-Version und Partitionshashes müssen weiterhin zur
Job-Zulassung passen; auch die tatsächlichen Validation-Bytes werden vor der
Vorhersage-Auswertung vollständig geprüft. Das Ergebnisartefakt wird aus den
Hub-eigenen Tenant-/Job-/Attempt-Identitäten abgeleitet, nicht aus einem
Worker-Pfad. Seine Metadaten müssen im gespeicherten Job-Ergebnis genau einmal
vorhanden sein. Nach dem Lesen erfolgt eine erneute Prüfung der Job-,
Attempt- und Dataset-Projektionen.

Die zwei Dateizugriffe liegen hinter `SpecialistPredictionStoragePort` (ISP/DIP).
`ConfinedSpecialistPredictionStorage` öffnet Dateien relativ zu festgelegten
Roots mit Directory-Deskriptoren und `O_NOFOLLOW`; FIFOs werden nicht blockierend
geöffnet und anschließend verworfen. Symlinks in allen Pfadkomponenten,
Hardlinks, Sonderdateien und Dateien außerhalb der Größenbegrenzung werden
verweigert. Diese Infrastruktur ist von der Job-Autorisierung getrennt (SRP).
Die bestehenden Repository-Entitäten bleiben SQLModel-basiert; diese bestehende
Kopplung wird hier erhalten und nicht durch einen Repository-Umbau erweitert.

108 lokale Tests bestanden in 18,32 Sekunden. Die neuen Fälle verwenden echte
SQL-Job-/Dataset-/Attempt-Persistenz und synthetische abgeschlossene Jobs.
Sie prüfen tenantgebundenes Lesen, manipulierte Kontroll- und Dateidaten,
doppelte Artefaktmetadaten, sichere Dateiöffnungen sowie die abschließende
Job-Prüfung. Ein früher abgelaufener Ausführungslease verhindert das Lesen
eines bereits abgeschlossenen Ergebnisses nicht; der Dienst erteilt keine
neue Ausführungs- oder Promotion-Berechtigung.

Der Lesedienst stellt rohe technische Ausgaben bereit, keine Produktions-
Evidence. Factory-Trainer/Predictor-Komposition, Dataset-Erstellung und
Delegation über die bestehenden Hub-Jobs bleiben offen. Vor einer Promotion
gelten weiterhin der konkrete Spezialisten-Vertrag, eingefrorenes Holdout,
Policy und gültige Hub-Evidence-Bindungen. GBMF-011 bleibt `partial`.

## Mehrdeutige Modellausgaben

Auch der konkrete `SpecialistDecisionContract` verweigert jetzt doppelte
JSON-Felder. Solche Ausgaben zählen im Factory-Benchmark als ungültige Fälle,
statt beispielsweise das letzte von zwei widersprüchlichen Labels zu werten.
Übermäßige JSON-Verschachtelung und ungültige Unicode-Texte werden als begrenzte
Vertragsfehler zurückgegeben. Sehr große Integer-Konfidenzen werden vor der
Float-Konvertierung verweigert. Die gültige Ausgabeform bleibt erhalten.

71 lokale Vertrags-/Benchmark-/Factory-/Scorer-/Job-Lesetests bestanden in
10,51 Sekunden. Diese Prüfung schließt eine Parser-Inkonsistenz, nicht die
noch offene produktive Factory-Komposition.

## Wiederholbare Katalog-Projektion

Die vorhandene `MlInternDatasetRepositoryBridgeService.sync` erhöht die
SQL-Dataset-Version nicht mehr, wenn alle zu projizierenden Katalogwerte
unverändert sind. Damit bleibt eine bereits zugelassene Spezialisten-
Dataset-Bindung bei einer identischen Wiederholung gültig. Tatsächliche
Metadatenänderungen werden weiterhin gespeichert und erhöhen die Version.
Die Katalog-Brücke behält ihre bisherige Verantwortung (SRP); es werden
keine zusätzlichen Scheduling- oder Worker-Aufgaben eingeführt.

17 lokale Katalog-/Brücken-/Dataset-Bindungstests bestanden in 7,71 Sekunden.
Die neuen Fälle verwenden den echten Dateikatalog und die echte SQL-Brücke.
Diese Voraussetzung für die Factory-Anbindung ist umgesetzt; die vollständige
Factory-Dataset-/Trainer-/Predictor-Komposition bleibt offen.

## Bytebindung der Katalogvalidierung

Die bestehende Katalogvalidierung vergleicht nun vor der Auswertung und vor
dem Speichern des Reports die tatsächlichen Train-/Validation-Dateihashes
mit den zugelassenen Partitionshashes. Ein unveränderter Metadateneintrag
genügt nicht, wenn sich die Datei geändert hat. Änderungen vor der Prüfung
liefern `dataset_partition_hash_mismatch`; Änderungen während der Prüfung
liefern `dataset_changed_during_validation`. Die Katalogvalidierung behält
ihre bisherige Verantwortung, ohne Scheduling oder Worker-Ausführung zu übernehmen.

19 lokale Katalog-/Brücken-/Dataset-Bindungstests bestanden in 6,96 Sekunden.
Die neuen Fälle ersetzen reale Dataset-Bytes vor beziehungsweise während
der Validierung und prüfen den verweigerten Reportabschluss. Die produktive
Factory-Komposition bleibt weiterhin offen.

## Factory-Anbindung an den echten Dataset-Katalog

Die Factory akzeptiert jetzt einen optionalen `SpecialistDatasetAdmissionPort`.
`HubSpecialistDatasetAdmission` übernimmt die bereits geprüften, festen
Train-/Validation-Partitionen über den bestehenden Katalog und die SQL-Brücke.
Der Hub-Katalog erstellt, speichert und validiert das Dataset; alle Kandidaten
des Zyklus erhalten anschließend dieselbe reale SQL-Dataset-ID. Holdout-Daten
werden dieser Trainings-Admission nicht übergeben. Der Port bleibt eng (ISP/DIP);
die Factory delegiert die Aufnahme und übernimmt keine Dateipersistenz (SRP).
Der Adapter verwendet bestehende Hub-Dienste, keine unabhängige Task-Queue.

Record-IDs und Reihenfolge müssen dem Split-Manifest entsprechen. Die
Partitionen sind vor der Katalogaufnahme auf jeweils 64 MiB begrenzt. Der
Katalog behält den Factory-Split statt erneut zufällig zu teilen; seine
normalen Secret-/PII-/Overlap-Prüfungen bleiben wirksam. Identische Wiederholung
verwendet dieselbe Katalog-ID und hält die SQL-Version stabil. Eine beobachtete
Änderung der Partitionshashes während der Aufnahme wird verweigert.

53 lokale Tests bestanden in 8,99 Sekunden. Ein Factory-Zyklus verwendet nun
den echten Dateikatalog und die echte SQL-Brücke, weiterhin synthetische
Trainer-/Predictor-Doubles und die echte Registry. Der Test liest beide
gespeicherten Partitionen, prüft ihre exakte Zugehörigkeit, den Holdout-Ausschluss
und die stabile Wiederholung. Negative Tests verweigern Holdout-Substitution
und Quotenüberschreitung vor der SQL-Aufnahme.

Offen bleiben die echte Trainer-/Predictor-Job-Komposition, separate
Holdout-Evaluationsdatasets, produktive Runtime-Artefaktregistrierung und
die versionsgebundene Übergabe vom Admission-Ergebnis zum späteren Job-Submit.
Gerade bei konkurrierenden Dataset-Änderungen reicht eine nackte Dataset-ID
nicht; die produktive Komposition muss die zugelassene Version beim Submit
erneut verlangen. GBMF-011 bleibt `partial`.

## Versionsbedingung beim Job-Submit

Die Dataset-Admission gibt jetzt ein unveränderliches
`AdmittedSpecialistDataset` mit ID und SQL-Version zurück. Die Factory
übernimmt die Version als `expected_dataset_version` in jeden Kandidatenjob.
Der bestehende v2-Job-Vertrag erlaubt dieses Feld optional und verlangt einen
positiven JSON-Integer; Boolean, Float, Null und Strings werden verweigert.
Die reine Feldvalidierung liegt in einem eigenen Helfer, damit der vorhandene
große `from_mapping`-Parser nicht weiter an Komplexität gewinnt (SRP).

Der produktive Hub-Control-Service vergleicht die Bedingung mit dem
tenantgebundenen Dataset vor Job-/Task-Aufnahme. Eine beobachtete Abweichung
liefert `dataset_version_conflict` (HTTP 409). Bestehende Aufrufer ohne das
optionale Feld behalten ihr bisheriges API-Verhalten. Der Check ergänzt die
bereits vorhandene immutable Spezialisten-Bindung vor Ausführung und die
Byteprüfung vor Worker-Submit; er ersetzt diese späteren Prüfungen nicht.
Es entsteht keine zusätzliche Queue oder Worker-Orchestrierung.

68 lokale Factory-/Admission-/Contract-/Hub-Control-/Dataset-Bindungstests
bestanden in 14,58 Sekunden. Nach Auslagerung des Feldvalidators bestanden
die betroffenen zehn Tests erneut in 5,41 Sekunden. Die echten SQL-Fälle
weisen nach, dass eine geänderte Version weder Job noch Task erhält, während
eine passende Version übernommen wird. Die Factory-Trainer-/Predictor-
Job-Komposition und der reale Modellzyklus bleiben offen; GBMF-011 ist `partial`.

## Begrenzte Trainingssubmission über den Hub

`HubSpecialistTrainingSubmission` verwendet ausschließlich den vorhandenen
Hub-Control-Service: idempotent `create_job`, tenantgebundenes `get_job` und
bei Timeout `cancel_job`. Ein Specialist-Trainingsauftrag ohne zugelassene
Dataset-Version wird vor dem Submit verweigert. Der Adapter fragt nur die
zurückgegebene Job-ID ab; fremde Korrelationen und unbekannte Statuswerte
werden verweigert. Er startet keine eigene Queue, Worker-Delegation oder
Retry-Orchestrierung.

Deadline, Poll-Anzahl und angefordertes Wartebudget begrenzen die Ausführung
auch bei einer stehengebliebenen injizierten Uhr. Ein Timeout fordert den
Abbruch des genauen Hub-Jobs an und liefert einen begrenzten Fehler mit Job-ID.
Die Abbruchanforderung ist noch kein Nachweis, dass ein laufender Worker bereits
beendet wurde; dessen Abbruch/Fencing bleibt beim bestehenden Hub-Workflow.
Die zurückgegebene abgeschlossene Job-ID ist ebenfalls noch kein geprüfter
`TrainingOutcome`. Die reale Artefakt-, Basemodell- und Ressourcenprojektion
bleibt als nächster Anschluss offen.

Der Control-Port enthält genau diese drei Methoden (ISP/DIP). Admission und
Scheduling bleiben im bestehenden Hub, die Submission verantwortet nur den
begrenzten Aufrufablauf (SRP). 43 lokale Tests bestanden in 15,29 Sekunden:
echter Dateikatalog, SQL-Dataset und Hub-Dry-Run, idempotente Wiederholung ohne
doppelten Task, Timeout-Abbruchanforderung mit fortschreitender oder eingefrorener
Testuhr sowie bestehende Control-/Factory-Regressionsprüfungen. Das sind
synthetische technische Beobachtungen; kein reales Modell wurde trainiert.
GBMF-011 bleibt `partial`.

## Hub-eigene Adapterzuordnung des Trainingsjobs

Der bestehende `RegistryTrainingResultPublisher` leitet die Adapter-ID für
Spezialisten-Trainingsjobs nun aus der Hub-Job-ID ab, entsprechend dem
bereits vorhandenen Worker-Transportvertrag. Eine vom Worker gelieferte
abweichende ID wird vor Dateiinspektion und Registry-Schreibzugriff verweigert.
Fehlt die ID, verwendet der Hub selbst den erwarteten Wert. Generische
Trainingsjobs behalten den bisherigen kompatiblen Ablauf.

Die bestehenden Artefaktbaum-Prüfungen, Tenant-/Owner-Scope, Job-/Attempt-
Storage-Bindungen und der Status `trained` bleiben wirksam. Die Änderung
bleibt in der Publikationsverantwortung (SRP); der Publisher übernimmt keine
Trainingssteuerung oder Promotion. Adapter-IDs sind Domain-Metadaten und
keine `SRC_*`-/`RUN_*`-Evidence-Identitäten.

12 lokale Publisher-/Submission-Tests bestanden in 6,59 Sekunden. Die neuen
positiven Fälle inspizieren synthetische Safetensors-/Config-Dateien und
schreiben die echte tenantgebundene Registry; die negative Prüfung weist
nach, dass eine fremde Adapter-ID keinen Registry-Eintrag erzeugt. Ohne
verifizierte Provenance bleibt der registrierte Adapter unverified und
`trained`, nicht produktiv freigegeben. Die vollständige TrainingOutcome-
Projektion und Factory-Trainer-/Predictor-/Runtime-Komposition bleiben offen.

## Fehlende Ressourcenmessungen

`ResourceUsage` kennzeichnet nicht erhobene Messungen jetzt als `None`/JSON
`null`, statt sie mit einem scheinbar gemessenen Nullwert auszugeben.
Konsumenten müssen diese Felder als unbekannt behandeln. Tatsächlich
gemessene Nullwerte bleiben darstellbar, beispielsweise fehlender GPU-
Speicherverbrauch bei CPU-Ausführung; sie ergeben aber allein keinen
Effizienz-Promotionsnachweis.

Die Ressourcenprojektion verweigert Boolean, negative, nicht endliche und
falsch typisierte Werte. Im bestehenden Promotion-Gate zählen nur endliche,
positive Vergleichswerte als Effizienzgewinn. Dadurch können fehlende
Trainingszeiten oder Parameterzahlen keine ansonsten gleichwertige
Vorgängerversion verdrängen. Validierung bleibt bei der Ressourcenprojektion,
die Vergleichsentscheidung beim reinen Promotion-Gate (SRP).

76 lokale Ressourcen-/Benchmark-/Factory-/Dataset-Admission-Tests bestanden
in 6,34 Sekunden. Sie prüfen unbekannte Messungen, alle Ressourcenfelder,
ungültige Werte, verweigerte Scheinverbesserungen und bestehende echte
positive Effizienzvergleiche. Die reale TrainingOutcome-Projektion und
Factory-Komposition bleiben offen; GBMF-011 bleibt `partial`.

## Technische Projektion abgeschlossener Trainingsresultate

`SpecialistTrainingOutcomeService` liest abgeschlossene tenant- und
ownergebundene Hub-Jobs. Er prüft den aktiven abgeschlossenen Versuch,
Dataset-Version und Partitionsbindung, die Hub-abgeleitete Adapter-ID,
Registry-Konfiguration und den tatsächlichen Artefaktbaum. Das begrenzt
eingelesene Trainingsmanifest muss hinsichtlich Job, Versuch, Fencing-Token,
Dataset und unabhängigem Hub-Basemodell-Snapshot übereinstimmen. Doppelte
JSON-Felder und nicht endliche JSON-Zahlen werden verweigert. Abschließend
werden Job, Versuch und Dataset erneut geprüft.

Der zurückgegebene `TrainingOutcome` enthält additiv die tatsächliche
`adapter_id`. Nur die inspizierte Artefaktgröße wird als Ressourcenmessung
übernommen; Trainingszeit, VRAM und Parameterzahlen bleiben unbekannt.
Lesen und Projektion bleiben getrennt von Submission, Registry-Schreiben
und Promotion (SRP). Der kleine Repository-Leseport und der injizierte
Dateizugriff erhalten testbare Grenzen (ISP/DIP); die vorhandene konkrete
Registry-Abhängigkeit bleibt erhalten.

91 lokale Outcome-/Submission-/Publisher-/Factory-/Ressourcentests bestanden
in 12,42 Sekunden; Ruff und `git diff --check` waren erfolgreich. Die neuen
Fälle verwenden echte SQL-, Katalog- und Registry-Dienste mit synthetischen
Modellartefakten. Manipulierte Dateien, Manifestbindungen, Dataset-Versionen
und fremde Owner werden verweigert. Das ist keine produktive Release-Evidence
und kein tatsächlicher Modelltrainingslauf. Factory-Trainer-Komposition,
separate Validierungs-/Holdout-Auswertung und Runtime-Anschluss bleiben
offen; GBMF-011 bleibt `partial`.

## Factory-Trainer-Anschluss an Hub-Jobs

`HubSpecialistTrainer` implementiert den bestehenden Factory-Trainer-Port.
Vor der Submission prüft er Dataset-Scope, die zugelassene SQL-Version sowie
Anzahl, Reihenfolge und kanonischen Bytehash der übergebenen Trainingsrecords.
Dataset-Admission und Trainer verwenden denselben begrenzten Serializer.
Die bestehende Hub-Admission prüft die Version erneut, der Worker-Transport
die tatsächlichen gespeicherten Partitionsbytes.

Der Adapter delegiert an `HubSpecialistTrainingSubmission` und liest danach
über `SpecialistTrainingOutcomeService`. Er besitzt keine eigene Queue,
keinen Worker-Dispatcher und keine Trainingsausführung. Drei kleine Ports
trennen Dataset-Lesen, Submission und Ergebnisprojektion (ISP/DIP); der
Adapter verantwortet ausschließlich die Factory-Anbindung (SRP). Die
SQL-Dataset-Entität als Rückgabetyp bleibt eine bestehende Persistenzkopplung.

Die positive Anschlussprüfung verwendet eine synthetische abgeschlossene
Submission mit echter SQL-/Registry-/Artefaktprojektion. Eine zusätzliche
Prüfung verwendet die echte Hub-Submission im Dry-Run: Ohne registrierten
Modelladapter wird kein TrainingOutcome erzeugt. Ein tatsächlich trainierter
Worker-Lauf und die produktive Gesamtkomposition sind weiterhin unverified.
Separate Validierungs-/Holdout-Jobs, Predictor-Komposition und Übernahme
der realen Adapterpfade in die Factory-Runtime bleiben offen. GBMF-011
bleibt `partial`.

50 Regressionstests bestanden im ersten 51-Test-Lauf. Nach Korrektur der
erwarteten Fehlercode-Zeichenfolge bestanden alle sieben Anschlussprüfungen
im überlappenden Nachlauf in 10,60 Sekunden. Ruff war erfolgreich.

## Gebundene Hub-Evaluationssubmission

Der öffentliche Trainingsvertrag akzeptiert additiv
`expected_adapter_sha256` für `evaluate_lora`: nur einen kanonischen
SHA-256-Wert und nur bei Evaluationsjobs. Der bestehende Worker-Transport
vergleicht ihn mit dem inspizierten Adapterbaum vor Workspace-Kopie und
Worker-POST. Beim Kopieren gelten weiterhin die Einzeldateihashes. Bestehende
generische Evaluationsaufrufe ohne diese optionale Bindung bleiben kompatibel;
der neue Spezialisten-Consumer verlangt die Bindung.

`HubSpecialistJobSubmission` verwendet den bisherigen begrenzten
create/get/cancel-Ablauf nun für Training oder Evaluation.
`HubSpecialistTrainingSubmission` bleibt als kompatibler Name mit
Trainingsstandard erhalten. Evaluation verlangt Spezialisten-Scorer,
Adapter-ID, Adapter-Hash und Dataset-Version. Polling und Abbruch bleiben
begrenzt; die vorhandenen Fehlercodes der Trainingssubmission bleiben
kompatibel. Es entsteht keine zusätzliche Queue oder Worker-Orchestrierung.

`HubSpecialistEvaluation` prüft den abgeschlossenen scoped Job gegen den
angeforderten vollständigen Request und die Ergebnis-Adapter-ID. Erst danach
liest der bestehende autorisierte Vorhersagedienst alle Rohoutputs; der
Job wird abschließend erneut geprüft. Kleine Submission-, Job- und
Prediction-Ports trennen Ausführung, Autorisierung und Artefaktlesen
(SRP/ISP/DIP). Die vorhandenen SQL-Entitätstypen bleiben als Kopplung erhalten.
Normale Inferenz benötigt weiterhin einen approved Adapter.

50 lokale Vertrags-/Transport-/Dataset-Versionsprüfungen bestanden in
6,41 Sekunden, darunter verweigerter Worker-POST bei geändertem Adapterhash.
29 weitere lokale Evaluations-/Submission-/Trainer-/Prediction-Prüfungen
bestanden in 19,76 Sekunden. Die positive Consumer-Komposition verwendet
synthetische abgeschlossene Submission mit echten SQL- und Datei-Services;
der Transport verwendet einen deterministischen HTTP-Testdouble.
Das ist keine produktive Evidence und kein echter Modelllauf.

Separate Factory-Datasets für Validierung und Holdout, die konkrete
Predictor-Komposition, die produktive Gesamtverdrahtung und Übernahme der
realen Adapterpfade in die Runtime bleiben offen; GBMF-011 bleibt `partial`.

## Getrennte, ausschließlich evaluierbare Factory-Datasets

`HubSpecialistDatasetAdmission.admit_evaluation` nimmt nur die vollständige
Validierungs- oder Holdout-Partition des festen Split-Manifests an, zusammen
mit der ursprünglichen Trainingspartition. Die vorhandene Katalogvalidierung
prüft beide Dateien einschließlich Secrets und Überschneidungen. Die
Auswertung verwendet die Validation-Datei; der Holdout wird niemals in die
Train-Datei geschrieben.

Trainings-, Validierungs- und Holdout-Datasets besitzen getrennte stabile
Katalogidentitäten. Da das bestehende SQL-Schema nach `content_sha256`
dedupliziert, verwendet die Brücke ausschließlich für Evaluationsdatasets
eine strukturierte Dataset-Identität aus beiden Partitionshashes und dem
geschlossenen Zweck (`ananta.specialist-evaluation-dataset.v1`). Das Feld
bezeichnet hier den vollständigen Dataset-Inhalt einschließlich Zweck;
die tatsächlichen Einzeldateihashes bleiben in `partition_hashes` erhalten.
Generische und bisherige Trainingsdatasets behalten ihre bisherige Identität.

Der Katalog persistiert den Zweck `specialist_evaluation`, die Partition,
Manifest- und Holdout-Digests. Er bewahrt diese Bindung bei späteren Splits
und verweigert Ersatz durch eine andere Evaluationsbindung. Der Hub verweigert
alle anderen Jobtypen sowohl bei Admission als auch unmittelbar vor Ausführung.
Dadurch kann auch ein generischer Trainingsrequest diese Datasets nicht nutzen.

Die Dataset-Admission verantwortet weiterhin Katalogaufnahme und Projektion;
der kleine reine Purpose-Helper verantwortet Validierung und Identität;
der Hub verantwortet die Ausführungsentscheidung (SRP/DIP). Die bestehenden
konkreten Katalog-/Brückenabhängigkeiten bleiben erhalten. Kein Worker erhält
Orchestrierungsverantwortung. Die Factory-Predictor-Portverdrahtung ist noch
offen; die Dataset-Funktion allein ist noch kein vollständiger Factory-Zyklus.

49 lokale Katalog-/Brücken-/Admission-/Hub-Regressionstests bestanden in
17,33 Sekunden. Ein zusätzlicher Ausführungs-Recheck bestand in 5,42
Sekunden: Ein zuvor zugelassener Trainingsjob wird vor lokaler Ausführung
verweigert, wenn das Dataset inzwischen für ausschließliche Evaluation
gekennzeichnet ist. Ruff und `git diff --check` waren erfolgreich. Dies sind
synthetische technische Beobachtungen mit echten Datei- und SQL-Diensten,
keine produktive Modell-Evidence; GBMF-011 bleibt `partial`.

## Partition-Predictor im Factory-Zyklus

Die Factory besitzt additiv einen kleinen `SpecialistPartitionPredictorPort`.
Er erhält Trainingscommand, tatsächlichen TrainingOutcome, festes Manifest,
Trainingsrecords und die ausgewertete Partition. Bestehende Predictor-Aufrufe
bleiben kompatibel; mit dem neuen Port ist kein Legacy-Predictor nötig.
Der Factory-Benchmark wertet weiterhin selbst die vollständigen Rohoutputs
gegen den konkreten Vertrag und die erwarteten Labels aus.

`HubSpecialistPartitionPredictor` verbindet die Evaluation-Dataset-Admission
mit `HubSpecialistEvaluation`. Er prüft Trainingsvertrag und Manifestbindung,
verwendet die tatsächliche Adapter-ID und verlangt vollständige Base-/Adapter-
Vorhersagen. Der Job trägt Dataset-Version, erwarteten Adapterbaumhash und
additiv `expected_base_model_sha256`. Der normale öffentliche Vertrag prüft
beide Modellhashes strikt; der Worker-Transport verweigert einen abweichenden
Hub-Basemodell-Snapshot vor Worker-POST. Ein gleichnamiges inzwischen
verändertes Basemodell kann dadurch das Trainingsergebnis nicht ersetzen.

Die Verantwortung für Dataset-Aufnahme und Evaluation bleibt hinter kleinen
Ports (SRP/ISP/DIP); der Predictor besitzt keine eigene Worker-Queue und
keine Trainings- oder Inferenzimplementierung. Die Factory wählt weiterhin
Kandidaten und entscheidet die Promotion im Hub. Unvollständige Vorhersagen
beenden den Zyklus vor Kandidatenregistrierung.

71 lokale Factory-/Vertrags-/Transport-Regressionsprüfungen bestanden in
3,81 Sekunden. Nach Ergänzung der konkreten Predictor-Komposition bestanden
37 Dataset-/Predictor-/Evaluations-/Digestprüfungen in 10,23 Sekunden.
Die neue Factory-Prüfung verwendet echte Katalog-/SQL-Datasets, aber
synthetische Trainer- und Evaluationsports; sie belegt noch keinen echten
Worker-Modelllauf. Die aktuelle Factory-Registrierung erzeugt weiterhin
logische Versionseinträge ohne tatsächliche Adapterpfade. Deren produktive
Übernahme einschließlich Tenant-/Owner-Scope und Evidence-Gates sowie die
produktive Gesamtkomposition bleiben offen; GBMF-011 bleibt `partial`.

## Vorbereitung realer Runtime-Kandidaten

`SpecialistRuntimeCandidateService.prepare` übernimmt einen tatsächlichen
Trainingsadapter als tenant-/ownergebundenen versionierten Spezialisten.
Vor dem Registry-Schreiben prüft er Benchmark-/Manifest-/Lineage-Bindungen
und liest den TrainingOutcome erneut über den autorisierten Outcome-Dienst.
Der neue Eintrag bewahrt die tatsächlichen Adapterpfade, Konfiguration,
Dataset-Bindung, Source-/Run-IDs, Provenance-Klassifikation und Release-Ziel
des ursprünglichen Registry-Eintrags. Er wird mit einem optimistischen
Registry-Versionscheck als `evaluated` vorbereitet. Gleiche Wiederholungen
verändern die Registry-Version nicht; geänderte Evaluationen werden verweigert.

Diese Vorbereitung erteilt keine produktive Freigabe. Sie stellt keine
Evidence-Identitäten aus und wertet ein unverified Trainingsresultat nicht
auf. Eine künftige Factory-Registration muss den vorbereiteten Eintrag an
die bestehende Hub-Evidence-Admission und Governance übergeben, bevor eine
Promotion die Runtime aktiviert. Die bisherige Factory-Registrierung ist
noch nicht auf diese neue Übergabe umgestellt.

Die Vorbereitung bleibt von Training, Evaluation und Promotion getrennt
(SRP); die bestehenden konkreten Registry-/Outcome-Service-Abhängigkeiten
bleiben als Kopplung erhalten. Zehn lokale Kandidaten-/Outcome-Prüfungen
bestanden in 12,02 Sekunden mit echten SQL-/Registry-/Artefakt-Diensten und
synthetischen Modelldateien. Sie prüfen Pfad-/Scope-/Provenance-Erhalt,
stabile Wiederholung und verweigerte Artefakt-/Lineage-/Outcome-Manipulation.
Ruff und `git diff --check` waren erfolgreich. Ein echter Modelllauf und
produktive Evidence fehlen weiterhin; GBMF-011 bleibt `partial`.

## Factory-Übergabe realer Kandidaten

Die Factory verwendet additiv `SpecialistCandidateRegistrationPort`.
TrainingOutcomes mit tatsächlicher Adapter-ID benötigen diesen Anschluss;
ein Rückfall auf die bisherige unscoped Referenzregistrierung wird vor der
Auswertung verweigert. Referenztests ohne tatsächliche Adapter-ID behalten
den bisherigen Ablauf.

`HubSpecialistCandidateRegistration` bindet die fachliche PromotionDecision
an den konkreten Benchmark-Digest und delegiert die erneute Datei-/Scope-
Prüfung an die Kandidatenvorbereitung. Abgelehnte Kandidaten werden mit
tenant-/ownergebundenem optimistischem Versionscheck abgelehnt. Fachlich
geeignete Kandidaten bleiben `evaluated`; das Factory-Ergebnis meldet
`specialist_production_promotion_pending`, solange die produktive
Evidence-/Governance-Freigabe fehlt. Der Anschluss besitzt keine eigene
Approval-Regel und erzeugt keine Evidence-Identitäten.

Der kleine Registrierungsport hält Hub-Workflow und Registry-Persistenz
getrennt (SRP/ISP/DIP). Die konkreten Abhängigkeiten des Registry-Adapters
bleiben erhalten. Die Factory besitzt weiterhin die Kandidatenauswahl und
fachliche Entscheidung. Produktive Hub-Evidence-Promotion, Gesamtverdrahtung
und echte Worker-Modellläufe bleiben offen; GBMF-011 bleibt `partial`.

40 lokale Factory-/Registrierungs-/Predictor-Regressionsprüfungen bestanden
in 11,35 Sekunden. Ein überlappender Nachlauf mit zusätzlicher Prüfung einer
fremden Kandidatenentscheidung bestand mit acht Tests in 11,02 Sekunden.
Die Modelldateien bleiben synthetisch; reale Registry-/SQL-Dienste prüfen
Pfaderhalt und Scope. Ruff, Todo-Konsistenz und `git diff --check` waren
erfolgreich. Scoped Aktivversionsauflösung und Rollback benötigen noch den
Anschluss an den neuen Registrierungsport.

## Scoped Aktivversion und Rollback

Der additive `SpecialistLifecyclePort` verbindet Aktivversionsauflösung und
Rollback mit `HubSpecialistLifecycle`. Beide verwenden den festen Principal
und das Task-Kind der Factory. Ist der reale Registrierungsport konfiguriert,
verweigert die Factory einen fehlenden Lifecycle-Port statt auf die unscoped
Referenzregistry zurückzufallen. Die bisherigen Referenzzyklen bleiben
kompatibel.

Der Registry-Rollback akzeptiert additiv einen optionalen Task-Kind-Filter.
Der Spezialisten-Anschluss verlangt genau das zugehörige Task-Kind, prüft
den ausgewählten Adapter vor Mutation und übergibt dessen Registry-Version.
Das Rollback-Ziel muss approved sein und zu Tenant, Owner, Basemodell und
Task-Kind gehören. Fehlt ein Vorgänger, bleibt das Ziel `base_model_only`.
Rollback erteilt keine neue Freigabe. Bestehende Registry-Aufrufe ohne den
optionalen Filter behalten ihre bisherigen Verträge.

Der kleine Lifecycle-Port trennt die Factory von scoped Registry-Zugriffen
(ISP/DIP); Auswahl und Rollback besitzen keinen Trainings- oder Evidence-
Ausführungspfad (SRP). Die bestehende konkrete Registry-Abhängigkeit des
Adapters bleibt erhalten. 32 Factory-Regressionsprüfungen bestanden im
ersten Lauf; nach Korrektur der synthetischen Fixture-Dateibindung bestanden
34 Lifecycle-/Registry-Prüfungen in 3,53 Sekunden. Die Fixtures sind explizit
synthetisch und stellen keine produktive Evidence dar. Ruff und
`git diff --check` waren erfolgreich; GBMF-011 bleibt `partial`.

## Aktuelle Hub-Registry-Prüfung für produktive Evidence

`HubRegisteredReleaseEvidence` implementiert den kleinen `ReleaseEvidencePort`
für die bestehende Promotion-Fassade. Er nimmt ausschließlich bereits
Hub-registrierte, vollständig gepinnte Run-Identitäten entgegen und prüft
die aktuellen unveränderlichen Bindungen über `require_run_result`:
Task, Assignment, Dispatch-Lease, Inputs, Quellen und Ergebnisdigest.
Anschließend verlangt er `verify_release_binding` mit Produktionsscope.
Unbekannte, geänderte, fehlgeschlagene, synthetische, Test- und lokale Runs
können dieses Gate nicht erfüllen. Source-/Run-Mengen müssen exakt den
Pins entsprechen; Tenant und Projekt der Pins müssen zusammenpassen.

Die Promotion-Fassade akzeptiert diesen Adapter additiv per Dependency
Injection. Der bestehende extern vertrauensbasierte ProvidedEvidenceRegistry-
Pfad bleibt kompatibel; er erhält dadurch keine automatische Hub-Verifikation.
Der Promotionsdienst prüft Tenant-Scope und Evidence sowohl beim Planen
als auch erneut vor Storage-/Registry-Schreibzugriff. Ein nach Erstellung
des Plans ungültiger Run wird dadurch vor Promotion verweigert.

Der Port trennt die Freigabepolitik von der Registry-Infrastruktur (ISP/DIP).
Der neue Adapter verifiziert ausschließlich vorhandene Bindungen und stellt
keine Identitäten aus (SRP). Die konkrete HubRegistry-Abhängigkeit des Adapters
bleibt erhalten. Die Factory muss die gepinnten Trainings-/Evaluationsruns
und den produktiven Evaluationsstore noch an diesen Anschluss übergeben;
die Vorbereitung allein erteilt weiterhin keine Freigabe.

35 lokale Evidence-/HubRegistry-/Promotion-Regressionsprüfungen bestanden in
3,69 Sekunden; nach Ergänzung der Tenant-Grenze und des Vor-Promotion-Rechecks
bestanden zwölf Anschlussprüfungen in 3,12 Sekunden und ein zusätzlicher
Widerrufstest in 3,16 Sekunden. Diese verwenden ausschließlich isolierte
Registry-/SQL-Policy-Fixtures, auch für die erlaubte Produktionsscope-Route.
Sie sind selbst synthetische technische Beobachtungen, keine Evidence eines
realen Produktionsreleases. Ruff und `git diff --check` waren erfolgreich.
Factory-Gesamtverdrahtung und tatsächliche Worker-Modellläufe bleiben offen;
GBMF-011 bleibt `partial`.

## Explizite Hub-Factory-Komposition

`build_hub_specialist_factory` verbindet die vorhandenen konkreten Hub-
Dienste mit einem festen Principal: Dataset-Katalog und SQL-Brücke,
begrenzt wartende Trainings-/Evaluationssubmission, geprüfte Outcomes,
vollständige Predictions, Partition-Predictor, reale scoped Registry-
Vorbereitung und Lifecycle. Artefakt- und Dataset-Wurzeln sowie der
unabhängige Hub-Modellkatalog werden explizit übergeben. Der Builder
erzeugt keine Worker, eigene Queue, Ersatz-Evidence oder Approval-Policy.

Die Kompositionsfunktion übernimmt ausschließlich Dependency-Verdrahtung
(SRP/DIP); die konkreten Infrastruktur-Abhängigkeiten sind bewusst auf
diesen Kompositionspunkt beschränkt. Die bestehenden Ports behalten
Admission, Training, Lesen und Governance als getrennte Verantwortlichkeiten.
Der produktive Hub-Control muss weiter vom Aufrufer passend konfiguriert
werden; die Komposition aktiviert keine deaktivierte Live-Ausführung.

25 lokale Kompositions-/Predictor-/Registry-/Lifecycle-/Trainer-/Eval-
Prüfungen bestanden in 26,37 Sekunden. Die neue Prüfung verwendet echten
Hub-Control, SQL-Katalogprojektion und Dry-Run-Ausführung: Ohne registrierten
Modelladapter wird kein TrainingOutcome und keine Promotion erzeugt.
Ein fremder Tenant kann weder als Aktivversion erscheinen noch zurückgerollt
werden. Dies ist eine technische synthetische Beobachtung, kein tatsächlicher
Modelltrainingslauf. Die Übergabe produktiver Evaluationen und gepinnter Runs
an die Hub-Evidence-/Governance-Promotion bleibt noch offen; GBMF-011 bleibt
`partial`.

## Nachverfolgbare Evaluationsjobs im Factory-Ergebnis

`HubSpecialistEvaluation.evaluate_result` liefert additiv die geprüften
Rohoutputs und eine eingefrorene technische Evaluationsreferenz aus dem
abgeschlossenen SQL-Job: Job/Version, aktiver Versuch, Dataset/Version,
Adapter-ID/-Hash, optionaler erwarteter Basemodell-Snapshot und Requestdigest.
Die bisherige `evaluate`-Methode liefert weiterhin ausschließlich Outputs.

Der Partition-Predictor verlangt diese Zuordnung und prüft Dataset-Version
sowie Adapter- und Basemodell-Bindung gegen seinen Auftrag. Seine Antwort
bleibt eine String-Sequenz und trägt zusätzlich die Evaluationsreferenz.
Die Factory behält diese Referenzen am Kandidaten getrennt nach Validierung
und Holdout und exportiert sie additiv im Zyklusergebnis. Legacy-Predictors
ohne Referenzen bleiben kompatibel.

Diese Metadaten sind Domain-Referenzen und keine `SRC_*`-/`RUN_*`-Identitäten.
Sie müssen für eine produktive Promotion noch gegen aktuelle Job-/Attempt-
Autorität und gepinnte Hub-Runs verifiziert werden. Der neue Ergebnisvertrag
erzeugt weder Evidence noch eine Freigabe. Die Auswertung bleibt beim
Hub-Consumer, die Nachverfolgung beim Ergebnisvertrag und die Auswahl bei
der Factory (SRP/ISP); bestehende SQL-Service-Kopplungen bleiben erhalten.

40 lokale Evaluations-/Predictor-/Factory-/Kompositionsprüfungen bestanden in
8,77 Sekunden. Die Receipt-Prüfung verwendet echte abgeschlossene SQL-Jobs
mit synthetischen Vorhersageartefakten; der Factory-Zyklus verwendet weiterhin
synthetische Modellports. Keine produktive Evidence; GBMF-011 bleibt `partial`.

Zwölf überlappende Export-/Factory-Prüfungen bestanden in 6,08 Sekunden.
Nach Ergänzung einer manipulierten Receipt-Dataset-Version bestanden vier
gezielte Predictor-Prüfungen in 6,27 Sekunden. Falsche Zuordnung und
Teiloutputs beenden den Zyklus vor Registrierung. Ruff und
`git diff --check` waren erfolgreich.

## Aktuelle Autorität technischer Evaluationsreferenzen

`SpecialistEvaluationReceiptReader` vergleicht die technische Referenz mit
dem aktuellen tenant-/ownergebundenen abgeschlossenen Evaluationsjob:
Jobversion, aktiver Versuch, Dataset-Version, Adapter-/Basemodellbindung und
Requestdigest. Anschließend liest der vorhandene Prediction-Consumer die
vollständigen Artefakte mit seinen aktuellen Attempt-/Dataset-/Byteprüfungen;
vor Rückgabe erfolgt erneut der Vergleich mit der Receipt. Boolean-Versionen
werden verweigert.

Die reale Factory-Komposition injiziert diesen Leser in die Runtime-
Kandidatenvorbereitung. Kandidaten mit Evaluationsreferenzen benötigen
genau Validierungs- und Holdout-Referenzen sowie einen Leser; ihre Adapter-
Bindung muss dem TrainingOutcome entsprechen. Ungültige Referenzen beenden
die Vorbereitung vor Registry-Schreiben. Der Recheck stellt keine Evidence
aus und erteilt keine produktive Freigabe. Für die spätere Promotion bleiben
die verbindliche Partitionszweckprüfung, EvaluationStore-Übernahme und die
Bindung an Hub-registrierte Produktionsruns erforderlich.

Jobautorität und Artefaktlesen bleiben hinter den vorhandenen kleinen
Ports getrennt (SRP/ISP/DIP). Die bestehende konkrete Reader-Abhängigkeit
der Kandidatenvorbereitung bleibt erhalten. 25 lokale Receipt-/Prediction-
Prüfungen bestanden in 12,89 Sekunden; zehn Kandidaten-/Kompositionsprüfungen
bestanden in 11,36 Sekunden, einschließlich verweigertem Registry-Schreiben
bei widerrufener Referenz. Echte SQL-/Dateidienste und synthetische Modell-
und Widerrufsfixtures; keine produktive Evidence. GBMF-011 bleibt `partial`.

## Verbindliche Partitionszweckprüfung vor Kandidatenregistrierung

Kandidaten behalten additiv den Split-Manifest-Digest ihres Factory-Zyklus.
Die reale Kandidatenvorbereitung übergibt dem Receipt-Leser für jede
Evaluation die erwartete Rolle, diesen Manifest-Digest und den eingefrorenen
Holdout-Digest. Der Leser verlangt die passende aktuelle tenant-/ownergebundene
Dataset-Version sowie den vollständigen persistenten Evaluationszweck.
Die Prüfung erfolgt vor und nach dem tatsächlichen Prediction-Lesen.
Eine Validierungsevaluation kann dadurch nicht als Holdout dienen; fehlende
oder falsche Manifest-/Holdout-Bindungen werden vor Registry-Schreiben
verweigert. Bisherige rein technische Receipt-Leseaufrufe ohne erwarteten
Zweck bleiben kompatibel.

Der Receipt-Leser verwendet einen kleinen Job-/Dataset-Leseport (ISP/DIP).
Dataset-Zweckprüfung und aktuelle Jobautorität bleiben seine einzige
Verantwortlichkeit; Benchmark-Auswertung und Promotion bleiben getrennt
(SRP). Der bestehende Runtime-Kandidatenadapter bleibt konkret an den
Receipt-Leser gekoppelt. 37 lokale Receipt-/Registry-/Predictor-/Factory-
Prüfungen bestanden in 19,76 Sekunden. Neue Fälle verwenden echte SQL-Jobs,
synthetische Prediction-Dateien und persistente Zweck-Fixtures. Ruff,
Todo-Konsistenz und `git diff --check` waren erfolgreich. Produktive
EvaluationStore-/RUN-/Governance-Promotion und tatsächliche Worker-
Modellläufe bleiben offen; GBMF-011 bleibt `partial`.

## Native Spezialisten-Berichte im Evaluationsspeicher

Die reine `specialist_evaluation_projection` bindet Benchmark, Lineage und
Qualitätsentscheidung an denselben Ergebnisdigest. Sie bewahrt den vollen
Benchmark und benennt den aggregierten Score ausdrücklich als
`specialist_macro_f1`. Es werden keine Base-/Adapter-Losswerte abgeleitet
oder erfunden. Qualitätszulassung und produktive Evidence bleiben getrennt;
jeder gespeicherte Bericht trägt `production_release_evidence: false`.

`SpecialistBenchmarkProjectionStore` persistiert diese maximal 128-KiB-
großen Berichte unter Tenant-/Owner-Digests. Die Domain-Report-ID ist aus
dem kanonischen Bericht berechnet und keine Evidence-ID. Interprozess-
Transaktion, vorhandene sichere Artefaktschreibfunktion und confined
Dateilesen schützen den Speicher. Unveränderte Wiederholungen schreiben
nicht erneut; geänderte Dateien oder doppelte JSON-Felder werden verweigert.

Die reale Factory-Komposition injiziert den Berichtsspeicher über einen
kleinen Save-Port in die Kandidatenregistrierung. Nach deren Autoritäts-
und Artefaktprüfung wird der technische Bericht gespeichert. Der Registry-
Evaluationsverweis bleibt bisher der Benchmark-Digest; der neue native
Bericht ist noch nicht die Evidence-Projektion der bestehenden Promotion-
Fassade. Diese Verknüpfung mit gepinnten Hub-Produktionsruns und Governance
bleibt offen. Kein gespeicherter Bericht erteilt eine Freigabe.

Reine fachliche Projektion und Infrastruktur-Speicher liegen in getrennten
Modulen (SRP); ein kleiner Save-Port hält die Registry-Workflow-Klasse vom
konkreten Speicher unabhängig (ISP/DIP). Die bestehende konkrete Artefakt-
Storage-Kopplung des Speichers bleibt erhalten. 13 lokale Speicher-/Registry-
Kompositionsprüfungen bestanden in 13,15 Sekunden, ein zusätzlicher
Manipulationstest in 4,98 Sekunden. Synthetische Modelldateien mit echten
SQL-/Registry-/Dateidiensten, keine produktive Evidence. GBMF-011 bleibt
`partial`.

## Registry-Verweis auf native Benchmark-Berichte

Die reale Kandidatenregistrierung bindet nach Speicherung den versionierten
Registry-Adapter und dessen Benchmark-Digest an die native Report-ID.
Der Berichtsspeicher persistiert diese technische Zuordnung im selben
Tenant-/Owner-Scope unter einer Interprozess-Transaktion. Eine gleiche
Wiederholung ist unverändert; ein anderer Bericht für dieselbe Adapter-/
Benchmark-Zuordnung wird verweigert.

`get_for_record` prüft die Zuordnung gegen den übergebenen scoped Registry-
Record und liest den vollständigen, hashgebundenen Bericht. Adapterhash,
Spezialisten-/Versionsalias, Basemodell, Methode und Benchmark-Digest müssen
übereinstimmen. Die Zuordnung ersetzt keine Hub-Evidence: Eine produktive
Promotion muss weiterhin den aktuellen Registry-Record mit Versionscheck,
die Qualitätsentscheidung und die Hub-registrierten Runs überprüfen.

Der kleine Berichtsspeicher-Port enthält nun Save und Bind für denselben
Persistenzablauf (SRP/ISP); die Registry-Workflow-Klasse kennt weiterhin keine
Dateipfade oder Speicherimplementierung (DIP). 14 lokale Speicher-/Registry-/
Factory-Kompositionsprüfungen bestanden in 14,97 Sekunden, einschließlich
stabiler Zuordnung und verweigerter abweichender Adapterbytes. Synthetische
Modelldateien mit echten SQL-/Datei-/Registry-Diensten; keine produktive
Release-Evidence. Ruff und `git diff --check` waren erfolgreich.
Produktive Hub-RUN-/Governance-Promotion bleibt offen; GBMF-011 bleibt `partial`.

## Native Promotionsvorbereitung und Run-Bindungen

`build_hub_specialist_promotion` verbindet native technische Benchmark-Berichte
mit der vorhandenen `MlInternEvaluationPromotionFacade`. Die Qualitätsentscheidung
kommt vom Hub; sie ist kein frei wählbarer Worker- oder HTTP-Approval-Parameter.
Der Bericht bleibt ausdrücklich `production_release_evidence=false`. Sein
Macro-F1 wird unverändert verwendet, ohne erfundene Loss-Werte oder eine
Umdeutung zum generischen Base-vs-Adapter-Score.

Vor Promotion werden der aktuelle scoped Registry-Eintrag, der neu berechnete
Benchmark-Digest, die vollständige Qualitätsentscheidung und beide persistierten
Evaluationsreferenzen geprüft. Die Referenzen müssen verschiedene Validation-
und Holdout-Jobs unter dem ursprünglichen Adapter bezeichnen. Der bestehende
Receipt-Leser prüft aktuelle SQL-Versionen, Dataset-Zweck und tatsächliche
Vorhersagebytes. Produktive Provenienz stammt zusätzlich aus der zugelassenen
Holdout-Evaluation, nicht aus dem technischen Bericht.

`SpecialistReleaseExecutionBindings` verlangt genau drei aktuelle Hub-Ausführungen:
Training, Validation und Holdout. Ihre Run-Bindungen haben diese Semantik:

| Run-Feld | Autoritative Hub-Projektion |
| --- | --- |
| `task_id` | TrainingJob `task_id` |
| `assignment_id` | TrainingJob `worker_job_id` |
| `dispatch_lease_id` | Aktiver TrainingAttempt `id` |
| `input_digest` | TrainingJob `request_digest` |
| `result_digest` | Kanonischer Digest des vollständigen `result_summary` |

Der Hub muss diese Runs vor Ausführung reservieren und ihre Ergebnisse unter
genau diesen Bindungen annehmen. Unabhängige erfolgreiche Runs können die drei
Ausführungen nicht ersetzen. Der Trainingsleser kontrolliert die tatsächlichen
Adapterbytes nochmals; regulär `evaluated` bleibt lesbar, `rejected` nicht.
Aktuelle Job-/Attempt-Projektionen werden nach der Evidence-Prüfung erneut
gelesen. Der produktive Builder akzeptiert ausschließlich den Hub-Registry-
Release-Adapter, keinen Legacy-Whitelist-Fallback.

Qualitätsentscheidung und Ausführungsbindungen werden vor der Vorbereitung,
vor Storage-Änderungen und unmittelbar vor der atomaren Registry-Promotion
erneut geprüft. Ein Retry nach erfolgreicher Promotion darf den nativen Bericht
nur unter einer exakt passenden atomaren Promotion-Historie lesen; die Registry
prüft weiterhin denselben Idempotency-Key, Approver, Grund und Evidence. Eine
isolierte Legacy-Approval ohne Promotion-Historie genügt nicht. Hub-Governance
bleibt eine notwendige vorgelagerte Autorität; der Builder erzeugt keine Grants.

SRP/OCP: Native Berichte, aktuelle Ausführungsprüfung und Registry-Mutation bleiben
getrennt; der bestehende Promotionspfad erhält einen optionalen Guard und ein
kleines Read-Port. ISP/DIP: Die neue Vorbereitung braucht nur lesende
Repository-/Outcome-/Receipt-Ports. Kopplung an bestehende ORM-Datenprojektionen
und die konkrete Registry-Mutation bleibt erhalten. Der produktive Builder bindet
bewusst den konkreten Hub-Release-Adapter, um die schwächere Legacy-Whitelist
nicht als Ersatz zu akzeptieren. Es gibt keine Worker-Orchestrierung und keine
von dieser Vorbereitung ausgestellten `SRC_*` oder `RUN_*`.

## Geschlossene Evidence-Projektion im LoRA-Worker-Vertrag

Training und Evaluation akzeptieren additiv `hub_evidence_assignment`.
`HttpMlInternTrainingWorkerPort` transportiert ausschließlich die vorhandene
Hub-Projektion aus `_hub_evidence_assignment`; er erstellt keine Identitäten.
Der gemeinsame Worker-sichere Vertrag prüft geschlossene Felder, Projektionsdigest
und die Bindung von `dispatch_lease_id` an den aktuellen `attempt_id`. Fremde
Versuche, zusätzliche Rechte, beschädigte Digests und explizites `null` werden
verweigert. Ohne das optionale Feld bleibt die bisherige Wire-Darstellung erhalten.
Der Worker prüft Syntax und Dispatch-Bindung; Registry-Existenz, Quellenbindung
und produktive Evidence bleiben ausschließlich Hub-Aufgaben.

Die Hub-Ausführung besitzt jetzt einen optionalen `evidence_lifecycle`-Port.
`SpecialistTrainingEvidenceService` reserviert über die Hub Registry vor der
Ausführung und der Control-Service persistiert die geschlossene Projektion, bevor
sie im Execution-Spec an den Worker übergeht. Erfolgreiche, fehlgeschlagene und
abgebrochene Versuche werden unter genau Task/WorkerJob/Attempt/Request-Digest
und dem kanonischen vollständigen `result_summary` abgeschlossen. Eine exakte
Completion-Wiederholung ist idempotent; veränderte Resultate werden verweigert.
Eine fehlgeschlagene Evidence-Completion erzeugt ein begrenztes
`evidence_completion_pending`-Audit-Ereignis. Die technische Job-Fertigstellung
allein erfüllt keinen Release-Gate; die Registry-Reservierung bleibt unverifiziert.

Der Context-Port erhält ausschließlich Hub-eigene Projekt-, Policy-, Revision-,
Runtime- und Environment-Bindungen sowie bereits zugelassene immutable Source-Pins.
Das Modell ist zusätzlich an den unabhängigen Model-Catalog gebunden. Dataset-
Quellen benötigen als Origin-Digest den kanonischen Digest von
`{"dataset_binding": specialist_dataset_binding(dataset)}`; Basismodell-Quellen
den Digest von `{"model_id": job.base_model, "snapshot_hash": model_digest}`.
So sind Dataset-Version und beide Partitionen beziehungsweise der benannte
Modell-Snapshot gebunden. Die Registry verifiziert jeden Source-Pin erneut.
Fehlende Inputs, veränderte Bindungen, fremde Projekte und abgelaufene Versuche
stoppen vor Ausführung. Die neue Hub-Service-SRP kapselt Evidence-Lebenszyklus;
die vorhandenen Execution-/Policy-Mixins behalten ihre bestehende SRP-Schuld,
erhalten aber nur kleine Reservierungs-/Completion-Anschlüsse.

Die produktive Zusammensetzung bleibt offen: ein konkreter serverseitiger
Context-/Source-Admission-Adapter und die Evaluations-Publikation müssen die
neu reservierten Hub-Referenzen übernehmen; das ist kein Legacy-Whitelist-Opt-in.
Ohne den optionalen Anschluss bleiben bestehende Jobs technische, unverified
Beobachtungen. Nachträgliche Run-Erzeugung bleibt verboten. Die aktuelle
Docker-Prüfung zeigt laufende Hub-/Worker-Container, aber keine DeviceRequests
für Worker Alpha; `nvidia-smi` beendet sich mit Code 139. Ein produktiver
Modelllauf oder Deployment wurde nicht durchgeführt. GBMF-011 bleibt `partial`.

Die native Promotion liest jetzt die unabhängig zugelassenen Evidence-Einträge
beider Evaluationsjobs. Job, Attempt, ursprünglicher Adapter, Artifact-Digest und
Basismodell müssen jeweils zum eigenen Receipt passen. Beide Einträge benötigen
gültige, nicht leere Source-/Run-Referenzen; ihre normalisierte Vereinigung geht
an die bestehende Prüfung der exakt drei Hub-Ausführungsbindungen. Der Holdout-
Eintrag bleibt Träger der Release-Metriken und Artifact-Referenzen. Die gespeicherten
Einträge werden nicht verändert. Das erzeugt weder Identitäten noch verifiziert es
einen reservierten Run als produktive Evidence. Kleine Read-Ports und ein eigener
Binding-Helper bewahren ISP/DIP und SRP; die bestehende AdapterRecord-Kopplung bleibt.

## Tests

```bash
cd docker/compose-next
docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm t-infra \
  sh -c "python -m pytest -q tests/test_specialist_model_factory.py tests/test_specialist_model_factory_e2e.py"
```
