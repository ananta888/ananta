# Todo-Wiederaufnahme nach Neustart

Stand: 2026-10-11. Der Neustart war bereits erledigt. Die gesamte Todo-Bearbeitung
ist nicht abgeschlossen; die folgende Legacy-Codeumstellung ist implementiert.

## Aktueller Prioritätsschritt: BPMN-Artefakt-Containeranschluss

2026-10-11: Die zuvor offene positive Artefakt-Abnahme läuft jetzt über getrennte
Hub-/Worker-Container, produktive registrierte Worker-Authentifizierung, echten
Native-Byte-Ingress, SQL-Assignment-/Lease-Authority und Hub-Result-Receipts.
Der Consumer liest und verifiziert die aufgenommenen Bytes. Unaufgenommene
Referenzen werden vor terminaler Task-/Receipt-Persistenz abgewiesen.
Der Hub lädt seinen privaten Signierschlüssel aus eigenem Tmpfs über die
produktive Konfiguration; der Worker erhält ausschließlich Verifikation.

Vollständige Container-/Chromium-Matrix: 22/22 bestanden in 67.00 Sekunden,
keine Case-Auswahl, vollständiges Cleanup. Zusätzlich 57 gezielte Compiler-/
Artefakt-/HTTP-/Ingress-/Runner-Tests bestanden in 10.14 Sekunden; weitere
92 Datei-/Transfer-/Input-/Todo-Prüfungen bestanden in 8.42 Sekunden
(insgesamt 149 unterschiedliche Tests). Der strengere Artefakt-Negativfall
behält konsistente Umschlagreferenzen bei und wird ausdrücklich mit
`native_artifact_hub_receipt_required` abgewiesen. Keine
produktive Release-Evidence und kein Deployment. BPMNX-007/014/015 bleiben
`partial`: reale Containerverlust-/Outbox-Recovery,
Packaging-/PostgreSQL-Abnahme sowie die vollständige CaseFlow-/Browser-Recovery-
Matrix sind noch nicht abgeschlossen. Dies sind konkrete nächste lokale
Anschlüsse; die gesamte Todo-Reihenfolge bleibt aktiv.

Der separate `--full-app`-Container startet inzwischen den echten Hub mit
`create_app(testing=False)`: Memory-Composition, Routen, Health/Login und
Auth-Denials ohne unautorisierte Task-Anlage bestehen mit Cleanup. Der
autoritative `ROLE`-Schlüssel und additive Native-Artefakt-`/api`-Routen sind
korrigiert. Eine ausgewählte Artefakt-Handoff/Denial-Regression besteht 2/2;
sie ist ausdrücklich keine neue vollständige 22-Fall-Abnahme.

## Positiver Full-App-Native-Autopilot abgeschlossen

`--full-app-autopilot` besteht mit der vollständigen Hub-App, echtem
Autopilot-HTTP-Tick, getrenntem Worker und Artefakt-Producer/Consumer in
35.26 Sekunden mit Cleanup. Geschlossene Ed25519-Aufträge binden Worker und
Ablaufzeit; aktuelle Assignment/Lease/Grant-Prüfung und Hub-Receipts bleiben
verbindlich. 989 Regressionstests, 66 abschließende gezielte Prüfungen und die
separate 22/22 Container-/Chromium-Matrix bestehen. Keine produktive
Release-Evidence: Graph-Admission und Ausführungshandler sind Test-Fixtures.
Der nächste BPMN-Anschluss ist dauerhafte Dispatch-Outbox-/Containerverlust-
Recovery; anschließend PostgreSQL/Packaging und vollständige Browser-Recovery.
Gate: `artifacts/test-gates/bpmn-full-app-native-autopilot.v1.json`.

## Dispatch-Journal implementiert; Containerverlust-Abnahme folgt

Der Hub persistiert vor Native-Worker-HTTP genau einen Tenant/Task-Sendeslot.
Parallele Dispatcher, Hub-Neuaufbau und Status-Reset erzeugen keinen erneuten
Versand. SQL-Reconciliation schließt nur aus tatsächlich persistierten,
assignment-gebundenen Hub-Receipts; unklare Ausgänge bleiben maschinenlesbar
unresolved. 994 Regressionstests und der vollständige Full-App-Containerlauf
mit accepted-Journal-Prüfung bestehen. Neuer Gate-Bericht:
`artifacts/test-gates/bpmn-native-dispatch-journal.v1.json`.
Nächste Arbeit: tatsächlicher Containerverlust während der Ausführung,
Worker-Result-Redelivery und begrenzte Scheduler-Anbindung. Keine komplette
Outbox-Recovery oder produktive Release-Freigabe behauptet.

## Reale Worker-Containerverlust-Grenze geprüft

`--full-app-worker-loss` beendet den Worker nach Native-Ausführung vor der
HTTP-Ergebnisantwort. Der Host bestätigt den tatsächlichen Docker-Exit 73;
der Hub bearbeitet weitere authentifizierte Ticks. Journal bleibt unknown,
Status-Reset erzeugt keinen zweiten Versand, Receipt bleibt aus und bounded
Reconciliation meldet unresolved. Der Workflow behauptet keinen Abschluss.
28.98 Sekunden mit vollständigem Cleanup; normaler Full-App-Native-/Artefaktlauf
besteht separat in 35.42 Sekunden. 35 gezielte Regressionstests bestanden.
Gate: `artifacts/test-gates/bpmn-worker-container-loss.v1.json`.
Nächste Implementierung: dauerhafte Worker-Result-Nachlieferung und exakte
Hub-Admission beim Wiederanlauf, danach Scheduler-Reconciliation. Verlust vor
Ausführung/innerhalb externer Tools und PostgreSQL/Packaging bleiben offen.
BPMNX-007/014/015 bleiben partial; keine Produktions-Release-Evidence.

## Native-Ergebnisspeicher und exakte Hub-Nachlieferung implementiert

Worker reserviert einen privaten SQLite-Ergebnisslot vor Ausführung und
persistiert das Resultat vor HTTP-Antwort. Signierter Abruf ruft keinen Consumer
auf. Hub-Recovery erlaubt nur den ursprünglichen Journal-/Assignment-Kontext
unter aktueller Lease/Grant/Registrierung; manuelle Review-Gründe bleiben
verweigert. Produktiver Worker benötigt eine explizite private Datenbank auf
einem persistenten Worker-Volume (`ANANTA_NATIVE_RESULT_STORE_PATH`).
1042 breite Regressionstests plus 69 gezielte Prüfungen bestehen. Normale
Full-App-/Artefakt-Publication (36.49 s) und reale Verlust-Containment (30.01 s)
bestehen mit Cleanup. SQLite-Neuöffnung/Worker-HTTP/Hub-SQL-Aufnahme verifiziert;
noch keine komplette Container-Neuerstellung mit anschließendem Abschluss.
Nächster Anschluss: Worker-eigenes Test-Volume, kontrollierte Neuerstellung und
Redelivery im Full-App-Containerpfad, dann Scheduler-/Retention-/Policy-Recovery.
Gate: `artifacts/test-gates/bpmn-native-worker-result-recovery.v1.json`.

## Vollständiger Worker-Neuaufbau und Nachlieferung verifiziert

`--full-app-worker-redelivery` besteht in 34.19 Sekunden: Worker persistiert
Resultat im eigenen Docker-Datenvolume, fällt vor Antwort aus und wird mit
anderer Containeridentität neu erstellt. Der tatsächliche Hub nimmt das alte
Resultat über produktiven Abruf/Forwarding/SQL auf und schließt ursprünglichen
Task, Journal-Claim und Workflow ohne Wieder-Ausführung. Normale Native-/
Artefakt-Regression besteht separat (35.52 s), 45 fokussierte Prüfungen bestehen.
Cleanup bestätigt auch entfernte Volumes. Gate:
`artifacts/test-gates/bpmn-worker-recreation-redelivery.v1.json`.
Nächste Arbeit: begrenzte Scheduler-Reconciliation, dann abgelaufene Leases/
policy-autorisierte Neuversuche, Retention-/Tool-Isolation und PostgreSQL/
Packaging. BPMNX-007/014/015 bleiben partial; keine Produktions-Release-Evidence.

## Opt-in Hub-Scheduler-Reconciliation abgeschlossen

Der bestehende Autopilot-Tick prüft nach ausdrücklicher Hub-Freischaltung
(`ANANTA_NATIVE_RESULT_RECOVERY_ENABLED`) maximal einen passenden Native-
Transport-Review-Task. Tenant/Task-SQL-Filter und aktueller Goal-/Team-Abgleich
bleiben aktiv; Tick-Zähler rotiert Kandidaten. Worker-/Stop-/Manual-Review-/
deaktivierte Pfade öffnen keine Recovery-Ports. Kein neuer Loop oder Versand.
Worker-Neuerstellung/Nachlieferung schließt jetzt über authentifizierten
Autopilot-HTTP-Tick mit recovered=1/dispatched=0 Task, Journal und Workflow.
37.92 Sekunden mit vollständigem Cleanup; 1037 breite und 34 gezielte Tests
bestehen. Gate: `artifacts/test-gates/bpmn-native-result-scheduler.v1.json`.
Nächste Arbeit: abgelaufene Lease und Hub-autorisierte Neuversuchs-Policy;
danach Retention/Tool-Isolation und PostgreSQL/Packaging. Keine Release-Evidence.

## Aktueller Implementierungsblock: Zero-Trust Memory

Der zuvor offene Legacy-Anschluss ist für Result-Memory-Leser/-Schreiber,
SQL-Memory-Tree/Jobs, Vault-Export, Worker-Memory/Session-Suche und die
hauseigenen PMC-/SGPT-/Retrieval-Caches auf Default-Deny umgestellt.
Der Hub besitzt nun einen konkreten SQL-Policy-Adapter mit aktueller
Task-/Project-/Actor-/WorkerJob-/Lease-Bindung. Result-Aufnahme erfolgt vor
WorkerJob-Terminalisierung; Lesezugriffe nutzen aktuelle Task-/Goal-ACLs.
Feature-, Approval- und Sensitivity-Booleans erteilen keine Memory-Rechte.

Kanalregister: 501 Komponenten in 17 Gruppen. AST-/CI-Gates prüfen das Register
und verhindern bekannte direkte SQL-Memory-Ports in Produktionsaufrufern.
Die Operator-Migration ist standardmäßig ein nicht schreibender Preview;
Apply/Rollback benötigt eine Batch-ID. Alte SQL-Einträge werden nur aus ihrem
tatsächlichen Task-Owner task-lokal und untrusted aufgenommen; unklare Owner
werden quarantined. Tree-/Export-/Cache-Altbestand ohne gesicherte Aufnahme
bleibt gesperrt und muss aus zulässigen Quellen neu aufgebaut werden.

Validierung: 424 zusammenhängende Regressionstests bestanden in 53.14 Sekunden,
44 zusätzliche Execution-Tracking-/Recovery-Tests in 3.32 Sekunden und 46
abschließende Policy-/Todo-Prüfungen in 4.78 Sekunden (mit Überlappung).
Insgesamt 469 unterschiedliche Tests bestanden. Ruff für die betroffenen
Memory-Komponenten, beide Source-Gates, Todo-Konsistenz und Diff-Prüfung bestanden.
Die bestehenden langen Zeilen und globalen Engines in den größeren Goal-/
Orchestrierungsservices bleiben dokumentierte SRP-/DIP-Schulden.

Die bestehende Legacy-Codeumstellung ist implementiert und lokal geprüft.
Der Hub-Scratchpad ist nun zusammengesetzt: Task-Terminal-Hook und
Sitzungs-`finally` bereinigen exakt gebundene RAM-Daten; Policy-Nachprüfung
verhindert Rückgabe/späte Schreibzugriffe nach gleichzeitigem Widerruf.
Alle Hub-Lifecycle-Endzustände und Legacy-Aliase sperren Dispatch-Memory.
ZAM-006 ist abgeschlossen. Die neue zusammenhängende Regression besteht
mit 859 Tests; Full-App-Bootstrap und gezielte Artefakt-Containerfälle bestehen.

Die übrigen übergeordneten ZAM-Aufgaben bleiben `partial`, weil ihre Kriterien über
Legacy-Code hinausgehen: externe CLI-/Provider-Zustände, Filesystem-Sandbox,
kaskadierender Widerruf/Löschung, echte PostgreSQL-
Prüfung und produktive Einführung bleiben separat offen. Die bisher ungenutzten
Worker-Memory-Klassen brauchen vor Aktivierung einen authentifizierten
Hub-Autorisierungs-Transport; ihr Default ist gesperrt. Kein Deployment und keine
produktiven Daten migriert. Tests sind synthetische technische Beobachtungen,
keine produktive Release-Evidence. `memory-session-*` sind ausschließlich
Hub-Memory-Berechtigungssitzungen, keine `SRC_*`-/`RUN_*`-Identitäten.

Details: `docs/security/zero-trust-agent-memory.md` und
`todos/kritis/todo.zero-trust-agent-memory-and-hidden-state.json`.

## Bereits dokumentierter Model-Factory-Stand

Model Factory: Der scoped Registry-Adapteralias und Benchmark-Digest sind an
einen nativen technischen Bericht gebunden. Der Leser prüft Tenant, Owner,
Adapterhash, Basismodell, Methode und Versionsalias. Wiederholte unveränderte
Bindung ist idempotent; abweichende Bindungen werden verweigert.

Abschlussprüfung: 14 Tests bestanden in 13.87 Sekunden:

```sh
/home/krusty/.cache/ananta-todo-venv/bin/python -m pytest -q tests/test_specialist_benchmark_projection.py tests/test_specialist_runtime_candidate.py tests/test_specialist_factory_composition.py
```

Todo-Konsistenz und `git diff --check` bestanden. Tests verwenden synthetische
Modelldateien und liefern keine produktive Release-Evidence. GBMF-011 bleibt
`partial`.

## Nächster Arbeitsschritt

Der Leseanschluss `SpecialistRegisteredBenchmarkReader` ist implementiert:
Er liest den scoped Registry-Eintrag vor und nach dem Bericht, verlangt Status
`evaluated` und die vollständige erwartete Qualitätsentscheidung. Abweichende
Policy-Digests, fremder Scope und Registry-Änderungen während des Lesens werden
verweigert. Die kleinen Read-Ports schützen ISP/DIP; AdapterRecord bleibt eine
bestehende Kopplung an das Registry-Datenmodell. Der Leser erteilt keine Approval.
Der Anschluss ist inzwischen als `build_hub_specialist_promotion` implementiert.
Native Macro-F1-Berichte, beide aktuellen Evaluationsreferenzen, erneut geprüfte
Trainingsbytes und exakt drei Hub-Run-Bindungen werden vor dem bestehenden
Release-Gate geprüft. 63 gezielte Tests bestanden in 34.27 Sekunden; synthetische
technische Beobachtungen, keine produktive Evidence.

Der Hub-Ausführungspfad besitzt inzwischen einen optionalen Evidence-Lifecycle-
Anschluss: Registry-Reservierung vor Ausführung, persistierte geschlossene
Worker-Projektion und gefenceter Abschluss unter Task/WorkerJob/Attempt/Request-
und Result-Digest. 41 Lifecycle-/Control-/Transportprüfungen bestanden in 23.09 s,
einschließlich Live-Policy mit synthetischem Mock-Worker, Fehler und Abbruch.

Nächster Factory-Schritt: konkreten serverseitigen Source-/Run-Context-Adapter
und die Evaluations-Publikation mit den neu reservierten Referenzen verbinden.
Der native Promotions-Leser übernimmt inzwischen die unabhängig zugelassenen
Referenzen beider Evaluationsjobs. Jeder Eintrag wird gegen sein eigenes Receipt
geprüft; die Vereinigung geht an die bestehende exakte Drei-Run-Prüfung. Die
Publikation aus den neu reservierten Hub-Projektionen bleibt anzuschließen.
Keine nachträgliche Run-Registrierung oder Whitelist-Aufwertung.

Gemeinsamer Abschlusscheck nach allen Änderungen einschließlich Hub-Lifecycle: 196 Tests bestanden in
59.41 Sekunden. Ruff, `git diff --check` und Todo-Konsistenz bestanden.
Keine produktive Freigabe und kein Deployment; GBMF-011 weiterhin `partial`.

Danach fehlen weiterhin produktive Training-/Evaluationsläufe, gebundene
Hub-RUN-Identitäten und Governance-kontrollierte Promotion. Keine Approval- oder
Evidence-Identitäten aus technischen Berichten ableiten.

Nach Ergänzung der Evidenz beider Evaluationsrollen bestanden 55 gezielte
Tests in 29.87 Sekunden. Ruff, Diff- und Todo-Konsistenzprüfung bestanden.
Die Tests bleiben synthetische technische Beobachtungen.

## Arbeitsstand bewahren

Änderungen liegen im lokalen Worktree, einschließlich zahlreicher neuer Dateien;
dieser Abschluss erstellt keinen Commit. Bestehende fremde Änderungen, unter
anderem `frontend-angular/package.json`, bewahren. Detailstand steht in
`docs/specialist-model-factory.md` und
`todos/active/todo.gate-benchmark-specialized-model-factory.json`.

Die bestätigte Reihenfolge bleibt BPMN, Meet, Pi, Model Factory,
Zero-Trust-Memory und anschließend die übrigen nicht archivierten Gruppen.
Bereits dokumentierte offene produktive Prüfungen werden durch diesen lokalen
Checkpoint nicht als erledigt markiert.


## Expired dispatch / exact Hub retry policy (2026-10-11)

Full application container gate passed in 40.45 seconds: real Worker recreation,
injected SQL execution-lease expiry, old result denied, signed cancellation from
transport review, unapproved retry denied and exact Hub-policy retry completed
with a fresh Task/attempt and higher fencing token. Old dispatch remains unknown.
Normal current-lease redelivery regression passed in 38.95 seconds; both gates
verified container/network/volume cleanup. Broad regression: 996 tests passed
in 66.45 seconds. Synthetic graph/handler, injected expiry and programmatic
signed controls remain explicit limits; no production release evidence.
See `artifacts/test-gates/bpmn-expired-dispatch-retry.v1.json`.
Next: retention maintenance and tool filesystem isolation, PostgreSQL,
production packaging/providers/release admission. BPMNX-007/014/015 stay partial.


## Worker result retention follow-up (2026-10-11)

Bounded startup/request-time expiry and a finite headless maintenance module
are implemented. Expired payloads/digests are scrubbed without content reads;
live results, replay tombstones and the store quota remain preserved.
Broad regression: 1009 passed in 68.41 seconds. Final focused check: 31 passed
in 4.99 seconds, with one additional test beyond the broad run. Isolated
networkless UID-65534 cached-image storage check: 23 passed in 1.13 seconds.
Full-app Worker recreation/redelivery passed in 38.81 seconds with cleanup.
The first minimal container mount set could not import the existing shared
Hub contracts; the successful check explicitly includes read-only agent code.
Standalone Native packaging debt remains; no production release claim.
Next: OS tool isolation, Hub/deployment-controlled idle maintenance cadence,
authority-bound tombstone retirement, PostgreSQL and production admission.
BPMNX-007/014/015 stay partial. Report:
`artifacts/test-gates/bpmn-worker-result-retention.v1.json`.


## Shell/private-store isolation follow-up (2026-10-11)

Mandatory Landlock (ABI >=6 plus erratum 3 fix) and metadata Seccomp now
protect the configured private result store at the shared Worker Shell port.
Pinned workspace FD, clean environment, closed unrelated FDs and no fallback.
Actual Full-App Hub/Worker dispatch/admission gate passed in 35.47 seconds:
workspace writes allowed, same-UID SQLite read/write/truncate/chmod denied;
native and artifact-handoff workflows completed. All project resources cleaned.
Broad regression: 1145 passed in 70.19 seconds; final focused: 56 passed
in 3.59 seconds, including the additional erratum/config-drift and 22 inventory tests.
Static synthetic graph/handler/commands remain explicit; no production release
evidence. Other provider/tool ports, ARM execution, network/resource profiles,
idle maintenance/tombstone retirement, PostgreSQL and release admission remain
open. BPMNX-007/014/015 and ZAM-007 stay partial. Report:
`artifacts/test-gates/bpmn-private-store-tool-isolation.v1.json`.

The active cached store remains authoritative after config removal. Ordinary
sandboxed Shell jobs cannot perform owner-store maintenance; idle housekeeping
needs an explicitly authorized owner/administration execution context.
