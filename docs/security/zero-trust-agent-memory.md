# Zero-Trust Agent Memory

## Stand und Grenzen

Die bisherigen Result-Memory-Leser/-Schreiber, der SQL-Memory-Tree, Vault-Export,
Worker-Memory/Session-Suche und die hauseigenen Kontext-/Antwort-/Retrieval-Caches
sind auf explizite Hub-Autorisierung umgestellt. Der frühere optionale Gateway
und die Boolean-Freigaben sind keine produktiven Allow-Pfade mehr. Ein konkreter
SQL-Policy-Adapter bindet die aktuelle Task-/Project-Zuordnung, Actor, WorkerJob
und aktive Dispatch-Lease; Hub-Lesezugriffe prüfen die aktuellen Task-/Goal-ACLs.
Die Umstellung dieser Legacy-Codepfade ist lokal implementiert. Sie ist keine
vollständige Umsetzung aller Zero-Trust-Aufgaben: externe CLI-/Provider-Zustände,
Filesystem-Sandbox, kaskadierender Widerruf und produktive Abnahme bleiben eigene
Arbeitspakete. Auf produktiven Daten wurde keine Migration ausgeführt.
Task-gebundene Result-Memory-Schreibsessions besitzen jetzt automatische,
auditgebundene Vorher-/Nachher-Snapshots und über Web/TUI abrufbare Hash-Diffs.
Dies erfasst noch nicht alle anderen Speicherkanäle oder Promotion-Workflows.
Der gemeinsame Worker-Shell-Port isoliert bei konfiguriertem privatem
Result-Store nun Tools durch Landlock und einen Seccomp-Metadatenfilter.
Workspace-Zugriff bleibt erlaubt; Result-Store, Worker-Elternprozess und
Dateirechtsänderungen bleiben gesperrt. Der echte Hub-/Worker-Containerlauf
prüft diese Grenze. Auch der Native-Pi-SDK-Prozess und dessen Versionsprüfungen
verwenden bei konfiguriertem privatem Store diese Grenze; lokale Tests prüfen
synthetische SDK-/Probe-Skripte, keine Live-Provider-Abnahme.
Andere Provider-/CLI-/Tool-Startpfade und eine vollständige
Filesystem-Sandbox bleiben offen; siehe die genaue Abgrenzung in
`docs/architecture/native-autopilot-dispatch.md`.
Die früheren offenen Umstellungsvermerke in den datierten Todo-Notizen sind
historische Zwischenstände; maßgeblich ist `legacy_completion_review_2026_10_11`.
Die breiteren ZAM-Aufgaben bleiben wegen ihrer weiteren Abnahmekriterien teilweise
offen. Das Legacy-Source-Gate erfasst auch typisierte Repository-Aliase,
Modulimporte und als Callback weitergereichte ungesicherte Lesemethoden.
Es prüft statischen Python-Code und ersetzt keine Laufzeit-Sandbox.
Der erneute Abschlusscheck umfasst 183 Storage-/Legacy-Tests und 160 Policy-/
Source-Gate-Tests; 13 Tests überschneiden sich, somit sind 330 verschiedene
Tests bestanden. Der Bericht bewahrt die vorherige breite Abnahme separat.
Der vollständige Hub-Bootstrap ist im isolierten Container mit `testing=False`
geprüft. Dabei gilt der produktive Konfigurationsschlüssel `ROLE`; `AGENT_ROLE`
ist nur bei fehlendem `ROLE` ein additiver Kompatibilitätspfad. Leere oder
Worker-Rollen können über den alten Schlüssel keine Hub-Rechte erhalten.

Kontrolliert werden beobachtbare Daten, Speicherzugriffe und Tool-Wirkungen.
Interne neuronale Aktivierungen, private Gedanken oder ein sogenannter J-Space
werden nicht ausgelesen. Weder ein erfolgreicher Test noch eine valide lokale
Audit-Kette beweist die Abwesenheit versteckter Zustände. Die Komponenten erzeugen
keine `SRC_*`-/`RUN_*`-Identitäten und liefern keine produktive Release-Evidence.

Der aktuelle erneute Legacy-Abschlusscheck umfasst **350 bestandene Tests**: 153
Security-/Boundary-Tests und 197 SQL-/Audit-/Tree-/Legacy-Service-/Todo-Tests.
Memory-Legacy- und Kanal-Source-Gates sowie Todo-Konsistenz bestehen. Dafür waren
keine weiteren Implementierungsänderungen erforderlich; produktive Daten wurden
nicht migriert. Die oben genannten separaten Zero-Trust-Arbeitspakete bleiben offen.

Beim abschließenden Scope-Abgleich bestehen erneut **345 Tests** (153 Security-/
Boundary-Tests und 192 SQL-/Audit-/Tree-/Legacy-Service-/Todo-Tests) sowie beide
Memory-Source-Gates und die Todo-Konsistenz. Der alte `remaining`-Vermerk im
Review vom 2026-10-10 ist ausdrücklich als überholter historischer Stand markiert;
`legacy_conversion_status` bestätigt den Abschluss der bestehenden Codepfade.
Die frühere 350-Test-Prüfung bleibt im Bericht separat erhalten.

## Assets, Angreifer und Trust Boundaries

Zu schützen sind Ergebnisse und Kontext anderer Tasks, tenantgebundene Daten,
Speicherrechte, Hub-Policy, Quellenprovenienz, Löschentscheidungen und Audit-Spuren.
Modelloutput, Memory-Inhalte, Skills, Tool-Ergebnisse und Providerantworten können
irreführende Instruktionen, gefälschte Scope-IDs oder kodierte Zustände enthalten.
Ein kompromittierter Worker oder ein externer CLI-Prozess kann zusätzliche Datei-,
Cache-, Netzwerk- oder Bibliothekskanäle verwenden. Ein privilegierter
Datenbankadministrator kann sowohl Inhalte als auch lokale Audit-Ketten verändern.

Der Hub bleibt Eigentümer von Authentifizierung, aktueller Run-/Assignment-Bindung,
Policy, Capability-Ausgabe und Lifecycle. Worker führen delegierte Arbeit aus;
Modelltext und Worker-DTOs sind keine Berechtigungsquelle. Der
`MemoryRunPolicyPort.current` muss den aktuellen authentifizierten Actor, den
aktiven Run und dessen tatsächliche Assignment-/Lease-Bindung aus Hub-Zustand
auflösen. `SQLMemoryRunPolicyRepository` ist der konkrete Hub-Adapter. Er prüft
persistierte geschlossene Policies und die aktuelle SQL-Zuordnung erneut.
Die generische Worker-Memory-Transportanbindung bleibt erforderlich, bevor die
bisher nicht produktiv instanziierten Worker-Speicher aktiviert werden dürfen. Die deterministischen Test-Doubles sind dafür ausdrücklich kein Ersatz.

`MemoryRunPolicy`, Capabilities, Namespace und Sicherheitsmetadaten sind immutable
DTOs. Ihre Typprüfung und ihr Digest prüfen Struktur und Änderungen; sie ersetzen
keine Authentifizierung oder Registry-Verifikation.

## Kanalregister und CI

`config/security/memory-channels.v1.json` ordnet die statisch beobachteten
503 Python-Komponenten in 17 konkreten Kanalgruppen zu. Pro Gruppe stehen Owner, bestehender
Scope, Persistenz, Leser, Schreiber, Löschweg und Trust-Grenze im Register.
Technische Control-Plane-Stores und Renderer-Caches sind getrennt von
Result-Memory, Memory Tree, Vector-Retrieval, Modell-Memory und Scratchpad beschrieben.
Gemischte oder noch nicht verifizierte Laufzeitgrenzen sind ausdrücklich markiert.

```bash
python scripts/check_memory_channels.py
```

Die Prüfung importiert keine Produktivmodule. Sie erkennt benannte
Memory-/Cache-/Vector-/Scratchpad-/Checkpoint-Komponenten, bestimmte
Storage-Bibliotheksimporte, konkrete SQL-Tabellendeklarationen, Prozess-Cache-Aufrufe und statisch sichtbare Datei-
Schreibmodi. Neue Dateien oder zusätzliche Speicherfamilien in bekannten Dateien
benötigen einen expliziten Registereintrag. Die CI-Datei
`.github/workflows/memory-channel-security.yml` führt diese Prüfung und die
isolierten Policy-Tests aus; SQL-Tests werden vom bestehenden Backend-Core-Gate
entdeckt.

Der Scanner ist eine Quellcodeprüfung, kein Sandbox-Beweis. Dynamische Dispatches,
`os.open`-Flags, Shell-/MCP-Zugriffe, Dateiumbenennungen, Bibliotheksinternals und
externe Provider-/CLI-Caches benötigen zusätzliche Admission, Runtime-Policies
und Snapshot-/Diff-Kontrollen. Ein registrierter Kanal erteilt keine Capability.

## Gateway und Scopes

`MemoryPolicyGateway` prüft Kanalregistrierung, authentifizierten Actor,
Run-Bindung, aktive Policy, Ablaufzeit, exakten Namespace, Operation und
Sensitivity-Grenze vor dem Speicherzugriff. Es gibt keine Wildcard- oder
Default-Capability. Fehlende Policy, Policy-Ausfälle, unbekannte Kanäle und
Audit-Ausfälle verweigern den Zugriff mit begrenzten Fehlercodes.

| Scope | Verpflichtende Namespace-Bindung |
| --- | --- |
| Run | Tenant, Workspace, Task, Run und unveränderte Goal-Bindung |
| Task | Tenant, Workspace, Task und unveränderte Goal-Bindung |
| Goal | Tenant, Workspace, Goal |
| Conversation | Tenant, Workspace, Conversation |
| Workspace | Tenant, Workspace |
| Global | Tenant; explizite globale Capability bleibt erforderlich |

Lesen, Anhängen, Ändern, Löschen und Promotion sind unabhängige Operationen.
Eine Append-Capability erlaubt kein Überschreiben. Promotion verlangt einen
einzelnen Scope-Schritt sowie getrennte Source-Promote- und Destination-Append-
Grants unter identischer vollständiger Policy. Diese Vorprüfung ist noch kein
persistierter, reversibler Promotion-Workflow und kein Provenienznachweis.
Memory-Trust-Metadaten verändern keine Systeminstruktionen oder Toolrechte.

## Scratchpad und Result-Memory

`EphemeralScratchpadService` leitet den Namespace ausschließlich aus der aktuellen
Hub-Policy ab. `InMemoryScratchpadStore` bietet Textwerte, Run-/Entry-/UTF-8-Byte-
Quotas und atomare Quota-Prüfung unter einem Lock. TTL beträgt standardmäßig
300 Sekunden, maximal 3600 Sekunden, und endet spätestens am Run-Deadline.
Es gibt weder einen Datei-/SQL-Backend noch automatische Promotion. Instanzen
teilen keinen impliziten Zustand über Containergrenzen.

`HubMemoryRuntime` komponiert den RAM-Store und den Service mit dem aktuellen
SQL-Gateway. Der gemeinsame Hub-Post-Commit-Hook entfernt bei allen terminalen
Task-Zuständen sämtliche Scratchpads des exakten Tenant/Project/Task-Namespace.
Das gilt auch für Timeout, Abbruch, Archivierung und Verifikationsfehler;
Legacy-Status-Aliase werden bei der Policy-Prüfung normalisiert. Fremde
Namespaces bleiben erhalten. Hub-Memory-Sitzungen verwerfen ihren Run im
`finally`, auch wenn der SQL-Widerruf fehlschlägt. Die Zusammensetzung erteilt
keine zusätzliche Scratchpad-Capability.
Lesen und Schreiben prüfen die aktuelle Policy nach dem RAM-Zugriff erneut;
ein währenddessen widerrufener Run liefert keinen Inhalt und behält keinen
späten Schreibzugriff. Eine gemeinsame transaktionale SQL/RAM-Fence ist dies
nicht. Die physische Bereinigung ist lokal zum besitzenden Hub-Prozess;
andere Prozesse/Container erhalten keinen impliziten gemeinsamen RAM-Zustand.
Terminaler SQL-Zustand sperrt aktuelle Worker-Grants unabhängig vom Cleanup.
Ein fehlgeschlagener Cleanup wird ohne Inhalte/Backend-Diagnostik gemeldet
und verhindert die bereits committed Task-Finalisierung nicht.
`reap_expired()` entfernt abgelaufene verwaiste Einträge. Nach Prozessneustart
gibt es keinen gespeicherten Scratchpad zurückzuladen. Ein terminaler Run verliert
bei der Policy-Prüfung sofort sein Leserecht. Die Bereinigung entfernt Python-
Referenzen; sie ist keine Zusicherung eines kryptografischen RAM-Wipe oder einer
systemweiten Swap-Kontrolle.

`ResultMemoryService` verwendet standardmäßig den aktuellen Hub-Gateway.
Ohne autorisierten Kontext entfällt die optionale Speicherung; es gibt keinen
ungeprüften Fallback. Explizit abgesicherte Aufrufer erhalten bei fehlender oder
abweichender Bindung einen begrenzten Fehler. Ein `approved`-Boolean macht einen
Worker-Inhalt nicht vertrauenswürdig.

`HubMemoryRuntime.worker_result` reserviert eine kurzlebige Memory-Berechtigung
unter dem authentifizierten Worker und der tatsächlichen Lease. Die Aufnahme
passiert vor dem Terminalisieren des WorkerJobs; danach ist die Berechtigung
widerrufen. Hub-Administratoren benötigen zusätzlich aktuelle Project-Schreibrechte.
`request_read` erstellt begrenzte Leserechte aus vorhandenen Task-/Project-/Goal-
ACLs und prüft sie bei jedem Zugriff erneut. `memory-session-*` bezeichnet nur
Hub-Memory-Berechtigungssitzungen, niemals Evidence-Registry-Identitäten.

`ScopedResultMemoryReader` ersetzt die direkten Repository-Leser in Goal-Service,
Task-Nachbarschaft, Retrieval/Query-Builder und Artifact-Tracking. Inhalt wird
erst nach Autorisierung über `get_by_namespace` gelesen. SQL filtert tatsächlichen
Task-Tenant, Project, Goal, Security-Scope und Sensitivity. Fehlende Legacy-
Namespaces bleiben ausgeschlossen. Rohmethoden sind ausschließlich interne
Persistenz-/Migrationsports; `scripts/check_memory_legacy_access.py` verhindert
bekannte direkte Produktionsaufrufe einschließlich lokaler Aliase.

Der Memory-Tree prüft alle Chunk-/Node-/Job-Operationen vor SQL und erneut vor
Commit beziehungsweise Rückgabe. IDs sind namespacegebunden, ungebundene alte
Zeilen werden ausgeschlossen. Source-, Topic- und Global-Zusammenfassungen haben
getrennte Task-/Workspace-/Global-Rechte. Ableitung in größere Scopes benötigt
jeden einzelnen Task→Goal→Workspace→Global-Promotionsschritt. Automatische
Result-Aufnahme in den Tree braucht eine unabhängige Tree-Capability.
Vault-Export braucht zusätzlich eine Export-Capability und einen vom Hub
konfigurierten `MEMORY_VAULT_EXPORT_ROOT`. Dateien liegen unter einem
Namespace-Unterverzeichnis, werden atomar mit Modus 0600 geschrieben und
bleiben explizit untrusted; Caller-Flags erteilen keine Exportrechte.

`WorkerMemoryAuthorizationPort` ist ein kleiner Ausführungsport ohne Hub-Import.
MemoryStores, SessionSearchIndex und RetrievalCache sind ohne authentifizierten
Hub-Transport gesperrt. Bestehende Feature-Booleans können Rechte einschränken,
aber nicht vergeben; Worker dürfen Langzeit-Proposals nicht selbst promoten.
RAM-Daten sind begrenzt, ablaufend, namespacegebunden und als Kopie zurückgegeben.
Diese Klassen besitzen derzeit keine produktiven Konstruktor-Aufrufer im
Repository; eine spätere Transport-Integration muss aktuelle Assignment/Lease-
Prüfung garantieren. Ein Testdouble ist dafür kein produktiver Ersatz.

`NamespaceMemoryCache` kontrolliert PMC- und hauseigene SGPT-Antwort-Caches:
Run-Namespace, vollständiger Policy-Digest, TTL und Payload-Hash müssen passen.
Alte flache Dateien sind keine Cache-Treffer. Read/Append/Update/Delete brauchen
getrennte Grants; Invalidation betrifft nur den eigenen Namespace.
`allow_sensitive=True` ist keine Berechtigung. Ohne Cache-Grant berechnet der
Aufrufer den Inhalt frisch. Provider- und externe CLI-Caches werden dadurch
nicht kontrolliert. Eigene bestehende Vector-/Dendritic-Control-Policies sind
keine impliziten Memory-Grants und werden hier nicht pauschal ersetzt.

Policy-Rechecks begrenzen Änderungsrennen, ersetzen aber keine gemeinsame
transaktionale Assignment-/Lease-Fence über Policy und alle Speicheradapter.
Ein Payload-Hash schützt gegen beschädigte Dateien, nicht gegen einen
privilegierten Angreifer, der Datei und Hash gemeinsam neu schreibt.

## Dauerhafter Audit

`MemoryAuditService` implementiert den verpflichtenden Gateway-Audit-Port über
`SQLMemoryAuditRepository`. Der Datensatz enthält Actor, Run, Task-/Trace-
Zuordnung, Kanal, Namespace, Operation, Entscheidung, Policy-Digest, Regel und
Zeit. Inhalte, Scratchpad-Werte und private Modellbegründungen werden nicht
übernommen. Ereignisse sind Policy-Entscheidungen, kein Beweis dafür, dass eine
nachgelagerte Speicheroperation erfolgreich committed wurde.

Head und Event werden gemeinsam committed. SQLite reserviert vor dem Lesen des
Heads die Schreibtransaktion; verschiedene Engines teilen die Datenbank-Fence.
Der PostgreSQL-Pfad verwendet eine tenantgebundene Advisory-Lock und Head-Row-
Lock. SQLite ist lokal mit konkurrierenden Repository-/Engine-Instanzen geprüft;
die echte PostgreSQL-Ausführung dieses neuen Adapters ist noch zu prüfen.

Die Hash-Kette erkennt veränderte Payloads, Reihenfolge, fehlende Ereignisse,
Head-Drift und manipulierte Task-/Trace-Indizes. Der Adapter hat keine Update-/
Delete-API. `verify(tenant_id=..., limit=...)` meldet eine zu große Prüfspanne als
unvollständig statt als erfolgreich. Es gibt noch keinen extern signierten
Checkpoint; ein privilegierter Angreifer kann eine vollständige lokale Kette
umschreiben. Der Default-Audit-Retentionszeitraum beträgt 90 Tage und ist getrennt
von Content-TTL konfigurierbar. Ein sicherer Prune-/Checkpoint-Mechanismus fehlt
noch; `retention_until` löst allein keine physische Löschung aus.

## Migration und Incident Response

`MemoryEntryRepository.migrate_security_metadata(dry_run=True, limit=..., after_id=...)`
liefert eine begrenzte Vorschau mit Änderungszahlen und nicht klassifizierbaren
IDs, ohne Metadaten zu schreiben. Der Apply-Pfad ist idempotent und klassifiziert
Legacy-Daten task-lokal und untrusted; Scope-/Approval-Labels erzeugen keine
größere Namespace-Autorität. Fehler in neuen Metadaten rollen die Apply-Batch
zurück. Mit optionalem `batch_id` legt Apply vorher/nachher-Metadaten im selben
SQL-Commit in `memory_migration_backups` ab. Der interne Operator-Pfad
`rollback_security_metadata(batch_id)` stellt eine ganze Batch atomar und
wiederholbar wieder her. Fehlende Datensätze, geänderte Task-/Goal-Bindungen oder
später veränderte Metadaten brechen die gesamte Rücknahme ab. Pro Batch werden
höchstens 1000 Einträge verändert; ein belegter Batchname darf nicht erneut für
Apply verwendet werden. Ein Dry-run erzeugt kein Journal. SQLite ist getestet;
der PostgreSQL-Lockpfad benötigt noch eine reale Integrationprüfung.
Das Journal enthält ursprüngliche Metadaten und gehört ausschließlich zur
Hub-Administration, ohne Worker-/Agent-Zugriff oder neue HTTP-Route. Seine
Retention und Löschung müssen vor produktiver Nutzung geregelt werden.
Mit `admit_namespaces=True` kommt die Namespace-Zuordnung ausschließlich aus
dem tatsächlichen SQL-Task-Owner einschließlich Tenant/Project/Goal. Unklare
Owner werden als `quarantined` markiert und erhalten keinen Namespace. Bereits
Hub-registrierte Aufnahmebindungen werden erhalten. Tree-/Export-/Cache-
Altbestände ohne gesicherte Namespace-Aufnahme bleiben gesperrt und müssen aus
zulässigen Quellen neu aufgebaut werden; Labels oder IDs reichen nicht als
Autorität. Die kaskadierende physische Quarantäne/Löschung aller Vector-/Export-
Ableitungen bleibt ein separates offenes Arbeitspaket.

Der headless Operator-Befehl schreibt standardmäßig nichts und erstellt keine
Tabellen. Apply benötigt einen Batchnamen; Rollback verwendet dasselbe Journal:

```bash
python scripts/migrate_memory_security.py --limit 1000
python scripts/migrate_memory_security.py --apply --batch-id <operator-batch> --limit 1000
python scripts/migrate_memory_security.py --rollback --batch-id <operator-batch>
```

Die Datenbank kommt aus `DATABASE_URL`; Zugangsdaten nicht in Berichte oder
Shell-Historie übernehmen. CLI-Tests verwenden ausschließlich isolierte SQLite-
Datenbanken. Auf laufenden Produktivdaten wurde keine Migration ausgeführt.

Bei einem lokalen Gateway-/Audit-Vorfall:

1. `reason_code` auswerten. Bei `memory_audit_unavailable` oder
   `memory_policy_unavailable` keine alternative ungeprüfte Speicherung zulassen.
2. Neue betroffene Memory-Grants über den zuständigen Hub-Policy-Owner widerrufen.
   Es gibt dafür noch keine neue Memory-UI oder generische HTTP-Route; keine
   nicht vorhandenen API-Endpunkte verwenden.
3. Über den Hub-Audit-Repository-Port nur den berechtigten Tenant und die
   Task-/Trace-Filter lesen. `verify`-Ergebnis sichern; ein unvollständiges oder
   beschädigtes Log nicht als valide Historie behandeln.
4. Betroffenen Run terminalisieren und dessen Scratchpad über den Hub-Lifecycle-
   Hook verwerfen. Content-/Tree-/Vector-/Export-Kopien und Provider-/CLI-Caches
   getrennt erfassen; globale kaskadierende Quarantäne ist noch nicht verfügbar.
5. Für eine Metadatenmigration zunächst Dry-run durchführen. Bei unklassifizierten
   Zeilen und ungeklärten Ableitungen Apply zurückhalten. Vor produktiver
   Einführung Backup/Restore, Hub-Policy-Adapter und Rollback prüfen.
6. Reproduktion ausschließlich mit synthetischen Policy-/Storage-Fixtures und den
   folgenden headless Tests durchführen. Ein Replay erteilt keine produktive
   Freigabe und erzeugt keine nachträgliche Hub-Run-Identität.

```bash
python -m pytest -q --confcutdir=tests/security \
  tests/security/test_memory_access_contract.py \
  tests/security/test_memory_policy_gateway.py \
  tests/security/test_ephemeral_scratchpad.py \
  tests/security/test_memory_channel_inventory.py \
  tests/security/test_memory_legacy_source_gate.py \
  tests/security/test_worker_memory_boundary.py \
  tests/security/test_memory_namespace_cache.py
python -m pytest -q tests/test_memory_audit_service.py \
  tests/test_zero_trust_result_memory.py tests/test_memory_policy_repository.py \
  tests/test_validate_todo_consistency.py
```

Tests decken jede Gateway-Operation und jeden Scope mit positiven/negativen
Controls ab, einschließlich Cross-task-Canary, Policy-Widerruf, SQL-Tenant-Drift,
Audit-Manipulation/-Lücke, atomarem Rollback und konkurrierenden Quotas/Appends.
Dateisystem-Sandbox, Prompt-Injection-Abwehr, Providerkontrollen, Operator-Timeline,
vollständige Provenienz und kaskadierender Widerruf sind noch keine durch diese
Suite bewiesenen Fähigkeiten.

## Struktur und erhaltene SOLID-Schulden

Verträge, Autorisierung, Compaction-Anschluss, RAM-Storage, SQL-Audit, SQL-Migrationsjournal und
Source-Scanner haben getrennte Verantwortungen. Domain-Services verwenden kleine
Policy-, Channel-, Audit- und Storage-Ports (SRP, ISP, DIP). Die Migration
ist vom Lese-Repository in einen eigenen Transaktionsadapter ausgegliedert;
bestehende Methoden bleiben kompatible Fassaden. Der vorhandene
ResultMemoryService bleibt ein größerer Compaction-/Persistenz-Service mit
`Any`-Repository und Legacy-Factory-Singleton; die bestehenden Repository-
Scope-/TTL-Filter und der konkrete SQLAlchemyStoreSupport behalten ihre
Infrastrukturkopplung. Die neue Zugriffsklasse kapselt Autorisierung, statt diese
Schuld durch weitere Policy-Logik im Compactor zu vergrößern. Bei der weiteren
Migration sind ORM-/Storage-Ports und eine gemeinsame Hub-Transaction-Fence den
zusätzlichen Direktzugriffen vorzuziehen.

Die bestehende SQL-/Factory-Kopplung der Tree- und Goal-Services bleibt erhalten
(DIP/SRP-Schuld). Die neue Autorisierung liegt in kleinen separaten Boundary-
und Runtime-Komponenten; eine spätere injizierte SQL-Unit-of-Work ist der
saubere Ersatz für globale Engines und große Orchestrierungsservices. Der
AST-Guard ist eine Quelltextprüfung, keine Sandbox für dynamischen Python-Code.

Der zusätzliche Task-Terminal-Hook ruft einen kleinen Hub-Composition-Adapter
auf; RAM-Löschung bleibt im Store und Policy-Prüfung im Gateway (SRP/DIP).
Die gemeinsame Lifecycle-Endzustandsliste verhindert voneinander abweichende
Goal-/Memory-Terminalregeln. Die größere Task-Post-Commit-Funktion erhält keine
eigene Storage- oder Authentifizierungslogik. Die erhaltene globale SQL-Engine-
Kopplung und fehlende gemeinsame SQL/RAM-Transaktions-Fence bleiben offen.

## Lokale Abschlussprüfung 2026-10-11

424 zusammenhängende Regressionstests, 44 zusätzliche Tracking-/Recovery-Tests
und 46 abschließende Policy-/Todo-Prüfungen (mit Überlappung) bestanden; insgesamt
469 unterschiedliche Tests. Ruff für die betroffenen Memory-Komponenten,
Kanalregister, Legacy-Source-Gate und Todo-Konsistenz bestanden. Kein Deployment
und keine produktive Datenmigration; sämtliche Prüfungen sind lokale technische
Beobachtungen mit synthetischen Policies.

Abschließende zusammenhängende Legacy-/Security-Regression: **859 bestanden**.
Die isolierte Full-App-Bootstrap-Abnahme und ausgewählte Native-Artefakt-
Handoff/Denial-Containerfälle bestehen mit Cleanup. ZAM-006 ist damit für den
Hub-Scratchpad abgeschlossen; dies aktiviert keine bisher ungenutzten Worker-
Speicher und erweitert keine Grants. Die verbleibenden größeren Zero-Trust-
Arbeitspakete sind keine ungeprüften Legacy-Result-Memory-Fallbacks.

## Projektgebundene Audit-Ansicht (2026-10-12)

`GET /api/projects/<project_id>/memory-audit/events` liefert das gemeinsame
Readmodel für Control Center → Memory-Zugriffe und TUI `:memory-audit`.
Der Endpunkt steht ausschließlich im Hub zur Verfügung. Der authentifizierte
Tenant und gegebenenfalls die Projektbindung kommen aus dem Request-Principal;
Projekt-Verwaltungsrechte werden vor jedem SQL-Zugriff erneut geprüft. Eine
widerrufene Mitgliedschaft sperrt auch die nächste Seite. Viewer-Rechte reichen
nicht. Fehlende Tenant-Bindungen und unbekannte Projektzugriffe bleiben gesperrt.

Optionale Filter: `run_id`, `task_id`, `trace_id`, `channel_id`, `scope`.
`page_size` ist auf 1..200 begrenzt; `after_sequence` setzt eine Seite fort.
Tenant-, Projekt- und Zusatzfilter greifen in SQL vor dem Seitenlimit. Der
Run-Filter bezeichnet den zugreifenden Run; ein Memory-Ursprungsrun kann davon
abweichen. Nicht zu einem Projekt bindbare Ereignisse erscheinen nicht durch
eine globale Fallback-Abfrage.

Die Projektion enthält nur Audit-/Policy-Metadaten: Actor, Operation, Freigabe
oder Ablehnung, Grund, Namespace, Security-Metadaten, Policy-Revision und Hashes.
Zusätzliche Payload-Felder und Memory-Rohinhalte werden nicht exportiert.
Freigabeereignisse belegen eine Policy-Entscheidung, keine erfolgreiche Mutation.
Die einzelnen Ereignis-Hashes werden geprüft; eine gelöschte historische Zeile
kann mit dieser gefilterten Seitenansicht nicht vollständig nachgewiesen werden.
`complete_chain_verified` bleibt ausdrücklich `false`. Inhalts-Diffs,
Vorher/Nachher-Snapshots und Ableitungsprovenienz sind weiterhin offene Funktionen.

Die Angular-Ansicht verwirft alte Projekt-Anfragen, leert die vorherigen Daten
beim Projektwechsel und hält angewendete Filter über die Seiten hinweg stabil.
Der TUI-Befehl ruft ausschließlich die Hub-API auf und öffnet deren Readmodel im
bestehenden Audit-Viewer. Keine Worker-Delegation, kein direkter DB-Zugriff und
kein LLM-Aufruf werden durch die Ansicht ausgelöst.

SRP/ISP/DIP: SQL-Selektion, Berechtigung/Projektion, HTTP-Adapter und Client-
Darstellung bleiben getrennt; Readservice und Projektzugriff hängen an kleinen
Ports. Die bestehende globale Engine-Bindung bleibt auf die HTTP-Komposition
begrenzt. Der große bestehende TUI-Command-Dispatcher bleibt SRP-Schuld; der neue
Handler liegt separat und wird nur über die Registry ergänzt.

Lokale Abnahme: 1833 Python-Regressionen in 94.99s, ein nicht aktivierter
Live-Hub-/Browser-Kanban-Test übersprungen, eine vorhandene Deselektion und vier
bestehende Startup-Deprecation-Warnungen. Danach bestehen 52 fokussierte Fälle
in 6.09s mit dem finalen TUI-Renderer. Angular: neun gezielte Tests sowie
29 Control-Center-/Routing-Regressionen (ein bestehender Skip), Typecheck und
Development-Build. Der Build meldet einen bestehenden ungenutzten RouterLink
in KnowledgeHygienePageComponent. Kanalregister und Legacy-Source-Gate bestehen.
Report: `artifacts/test-gates/memory-audit-operator-access.v1.json`.

## Automatische Result-Memory-Snapshots und Diffs (2026-10-12)

Der Hub erfasst vor und nach `HubMemoryRuntime.worker_result` den erreichbaren
Task-Result-Memory-Bereich. Das gilt auch bei fehlerhaftem Verlassen der Session.
Die Session ist an eine tatsächlich reservierte Hub-Memory-Policy gebunden;
Tenant, Projekt und Goal müssen weiterhin dem Kontroll-Task entsprechen.
Ein Snapshotfehler vor dem Eintritt verhindert den Memory-Schreibpfad. Nach
dem Eintritt werden Snapshot-Erfassung und Grant-Widerruf unabhängig durch
verschachtelte `finally`-Blöcke abgesichert. Fehlt die Nachher-Beobachtung,
bleibt der Diff unvollständig; ein fehlender Zustand wird nicht rekonstruiert.

`memory_snapshots` speichert nur Entry-IDs, Inhalts-/Metadaten-Hashes,
Security-Metadaten und die gebundene Namespace-/Policy-Projektion. Die
Snapshot-IDs sind normale Hub-Domänenreferenzen, keine SRC/RUN-Identitäten.
Der Quelladapter filtert Namespace und Sensitivitätsgrenze bereits in SQL.
Maximal 1000 Einträge und 4MiB Eingabezeichen werden vor der Hydrierung geprüft.
Eine zusätzliche geschlossene Ergebnisvalidierung schützt auch bei einem
anderen Quelladapter gegen Rohinhalte, doppelte IDs und höhere Sensitivität.

Snapshot und referenziertes Audit-Ereignis werden in derselben SQL-Transaktion
geschrieben. Wiederholungen erhalten die erste unveränderliche Beobachtung;
ein anderer Outcome überschreibt sie nicht. Lesen prüft Snapshot-Hash,
SQL-Scope und die exakte Audit-Verbindung. Fehlende/manipulierte Audit-Ereignisse
sind ungültig. Die bestehende vollständige Tenant-Auditprüfung bleibt getrennt.

`GET /api/projects/<project_id>/memory-audit/diffs?run_id=<memory-session-id>`
verwendet dieselben Projekt-Verwaltungsrechte wie die Audit-Ansicht. Kanal,
Task, Trace und Scope können eingegrenzt werden. Die Angular-Ansicht bietet
„Vorher/Nachher-Diffs“; TUI:

```text
:memory-audit project-a --view diffs --run-id memory-session-example --channel-id hub-result-memory
```

`added`, `modified` und `deleted` beschreiben die beobachtete Eintragsmenge.
`deleted` bedeutet „nicht mehr im beobachteten Bereich“, keinen Nachweis einer
physischen Löschung. Ein Diff beweist nicht alleinige Verursachung durch einen
Run; parallele Änderungen können enthalten sein. `scope_outcome` beschreibt
die Memory-Session, nicht den Gesamtstatus des Tasks. Unveränderte Inhalte und
Metadaten erscheinen nicht als Änderungen. Rohinhalte werden nicht ausgegeben.

SRP/DIP/ISP: Quellenlesen liegt im eigenen SQL-Adapter hinter einem kleinen
Port; der Snapshot-Store verantwortet atomare Persistenz/Auditbindung, der
Diff-Vergleich ist rein und die Hub-Komposition bleibt Eigentümer des Ablaufs.
Die ältere globale Engine-Komposition im HTTP-Adapter bleibt erhaltene Schuld.

ZAM-009/011 bleiben insgesamt partial: weitere persistente Kanäle, verifizierte
Promotion-/Ableitungsketten, sichere Snapshot-/Audit-Retention und unabhängige
Produktions-/PostgreSQL-Abnahme sind nicht durch diesen Task-Result-Memory-Pfad
erledigt. Report: `artifacts/test-gates/memory-result-snapshots-and-diffs.v1.json`.

## Result-Memory: Hub-Zugriffsherkunft und lokale Ableitung

Neue autorisierte Result-Memory-Einträge enthalten geschützte
`memory_provenance`-Metadaten. Akteur, Memory-Session, Namespace, Operation,
Policy-Revision und Policy-Digest stammen aus der aktuellen Hub-Entscheidung.
Der SQL-Audit-Adapter liefert seine tatsächlich persistierte Ereignisreferenz
zurück; der Gateway prüft die geschlossene Receipt-Struktur vor der Freigabe.
Ein fehlerhaftes Receipt blockiert den Zugriff. Receiptlose bestehende
Testadapter bleiben kompatibel, ihre Herkunft ist explizit `unverified`.
Caller-Metadaten dürfen die geschützte Projektion nicht überschreiben.

`decision_audit_ref` verweist auf die Zugriffsentscheidung, nicht auf eine
atomare Quittung der späteren Eintragspersistenz. Die Referenz ist eine normale
Audit-Domänenreferenz, keine Registry-Evidence-Identität. `audit_referenced`
bedeutet daher weder verifizierte Quellenherkunft noch erfolgreiche Promotion.
Die älteren Angaben `generated_by`, `approved` und `confidence` bleiben
unbestätigte Kompatibilitätsangaben; sie ersetzen den Hub-Akteur nicht.

Die lokale Ableitung hält Hashes der gemäß Redaktionspolicy verarbeiteten
Eingabe, der tatsächlich gespeicherten Zusammenfassung und des gespeicherten
Inhalts fest. Transformation `worker_result_compact_v3` und normalisierte
Transformationspolicy sind ebenfalls gebunden. Es werden keine zusätzlichen
Rohinhalte oder Hashes der Eingabe vor der Redaktion in der Herkunft abgelegt.
Die Metadaten beweisen nicht die Richtigkeit des Worker-Ergebnisses:
`grounding_state=unverified` und `production_release_evidence=false` bleiben
fest gesetzt. Bei ausdrücklich deaktivierter Redaktion bezieht sich der
Eingabehash auf die von dieser Policy erlaubte Speicherrepräsentation.

SRP: Die reine Herkunfts-/Ableitungsprojektion liegt außerhalb der bestehenden
Result-Komprimierung. DIP/ISP: Der Audit-Port liefert nur die Ausstellerquittung;
die vorhandene globale Repository-Auflösung bleibt technische Schuld. Der
bereits große ResultMemoryService verbindet weiterhin Komprimierung und
Persistenz (erhaltene SRP-Schuld); eine spätere Extraktion des Komprimierers
soll diesen Umfang reduzieren, ohne die Schreibverträge zu ändern.

ZAM-010 bleibt partial: Registry-validierte Quellreferenzen, Modell-/Prompt-/
Profil-/Skill-Versionen, Memory-Elternketten mit Zyklusprüfung sowie alle
weiteren Kanäle und Export-/Importpfade sind weiterhin offen. Report:
`artifacts/test-gates/memory-access-origin-and-derivation.v1.json`.

## Memory-Tree: beobachtete Chunk-Eltern und aktualisierte Herkunft

Der SQL-Store ergänzt Source-/Topic-/Global-Nodes um geschützte Hub-Herkunft
und eine lokale Ableitungsbeobachtung: Summary-Hash sowie IDs und Inhalts-Hashes
der vollständig geladenen, autorisierten Chunk-Eltern. Rohinhalte gelangen
nicht in diese Projektion. `attribution=observed_parent_links` beschreibt die
gespeicherten Verknüpfungen; es beweist weder die semantische Richtigkeit noch
die ausschließliche Verursachung einer Zusammenfassung. Bestehende additive
Elternlinks bleiben aus Kompatibilitätsgründen erhalten.

Ein Update prüft die gesamte effektive Elternmenge, einschließlich bisheriger
Verweise. Namespace und Sensitivität werden vor der Hydrierung in SQL gefiltert.
Die Prüfung verwendet einen begrenzten Batch unter einem aktuellen Read-Grant;
maximal 2000 Eltern sind erlaubt. Fehlende oder fremde Eltern verweigern das
Update. Eltern müssen Chunks sein; Node-zu-Node- oder Self-Links werden nicht
als Chunk-Abstammung akzeptiert. Das ersetzt keine allgemeine Zyklusprüfung für
künftige Memory-zu-Memory-Ableitungen.

Aktualisierungen übernehmen nun die neuen fachlichen Metadaten und ersetzen
deren geschützte Felder durch die aktuelle Hub-Entscheidung. Die Herkunft nennt
die tatsächliche Operation `update`; der Update-Grant wird vor dem Commit
erneut geprüft. Ein Fehler rollt Summary, Eltern und Metadaten gemeinsam zurück.
Receiptlose Testadapter und alle lokalen Chunk-Beobachtungen bleiben explizit
unverifiziert. Freie `provenance_refs` sind weiterhin unbestätigte Fachreferenzen,
keine Evidence-Registry-Identitäten.

SRP: Elternauflösung und reine Hash-Projektion sind getrennt; die ältere globale
Engine-Auflösung im SQL-Store bleibt erhaltene DIP-Schuld. ZAM-010 bleibt
partial: komplette Hersteller-/Quellversionen, Registry-Bindung, allgemeine
Ableitungsketten und Export-/Importprüfung sind weiterhin offen. Report:
`artifacts/test-gates/memory-tree-parent-derivation.v1.json`.

## Vault-Export: Herkunft bleibt erhalten

Chunk-Markdown enthält zusätzlich `memory_access_origin` mit der gespeicherten
Quell-Herkunft, `export_access_origin` mit der aktuellen Hub-Vault-Entscheidung
und einen SHA-256 des exportierten Inhalts. Die beiden Entscheidungen bleiben
getrennt: eine Exportfreigabe bestätigt nicht die Quellenherkunft. JSON-Werte
werden als einzelne Frontmatter-Zeilen serialisiert. Fehlende ältere Herkunft
bleibt `null`; sie wird nicht aus der Exportentscheidung rekonstruiert.
`grounding_state=unverified`, `trust_level=untrusted` und
`production_release_evidence=false` bleiben feste Exportmarkierungen.

Der Export akzeptiert jetzt optional einen kleinen Chunk-Read-Port (DIP/ISP).
Die bestehende globale Store-Komposition bleibt als Kompatibilitätsfallback.
Die reine Herkunftsprojektion ist vom Dateischreiben getrennt (SRP). Bestehende
Namespace-, Pfad-, Sensitivitäts- und laufende Grant-Prüfungen gelten weiterhin.
Ein Importpfad und die vollständige Registry-Prüfung sind dadurch noch nicht
implementiert. Report:
`artifacts/test-gates/memory-vault-origin-preservation.v1.json`.

### Structured untrusted memory prompt data (2026-10-13)

The structured ContextBundler prompt path serializes memory chunks as bounded JSON with fixed untrusted/no-authority labels, descriptive source/provenance references, scope and local advisory injection signals. Angle brackets and embedded newlines are escaped, so stored content cannot close the data block. The instruction that memory cannot grant permissions remains outside that block. Memory Tree views expose untrusted labels and preserve provenance for chunks and summaries. This is structural separation, not a claim of model immunity to prompt injection. Unstructured legacy prompt paths and complete quarantine/cache enforcement remain open.

A separate sensitivity bridge maps the legacy `project_internal` label to memory `internal` and the inverse for the legacy context contract. Current Hub grants and namespace checks still apply. SQL tests verify that revocation denies a write despite `approved=True`; context-policy regression fixtures now supply explicit test-only Hub grants. SRP is protected by pure serialization and compatibility modules; the existing broad ContextBundler facade remains structural debt. The broad regression passes 476 checks, with 68 overlapping final followup checks including the new SQL test. See `artifacts/test-gates/memory-untrusted-prompt-data.v1.json`.

### Retrieval text and final prompt snapshot binding (2026-10-13)

Text-only retrieval consumers now receive the same escaped untrusted memory representation as structured consumers. After policy filtering, chunk limits and budget admission, ContextBundler rebuilds the text aggregate from the final selected chunks whenever the input contains memory. Caller-provided aggregate text cannot reintroduce denied or dropped memory. Memory wrapper and control-rule costs participate in budget admission, and text token estimates describe the reconstructed representation. Non-memory aggregate compatibility remains unchanged.

PromptSnapshotService adds an optional extension with exact character offsets and SHA-256 digests for each serialized memory block, content and descriptive references. Snapshot metadata stores neither memory text nor raw reference values. The ordinary memory-entry reference remains distinct from the normalized retrieval chunk reference. These observations bind the prompt representation; they do not validate Registry identities, grant trust or satisfy production evidence gates.

The final relevant regression passes 616 tests with one deselected case in 27.77 seconds. Legacy retrieval fixtures now provide explicit synthetic read-only grants and implement namespace reads. Worker plain ContextBlock and handoff consumers, complete quarantine/cache enforcement and productive verification remain open. SRP: representation helpers remain pure; retrieval, policy/budget selection and final snapshot recording stay in their respective services. Existing broad ContextBundler structure remains debt. See `artifacts/test-gates/memory-untrusted-text-snapshots.v1.json`.

### Worker context serialization (2026-10-13)

Core GroundedPromptAssembly, Hermes ContextBlock conversion and Coding prompt assembly now use a Worker-local pure quoted-data serializer. It redacts body and descriptive origin/provenance references, escapes role/block delimiters and fixes trust to untrusted with no authority and no verified identity. Core context binding labels and Coding retrieval traces also remain data. Existing injection and sensitivity checks still run before rendering; no Hub capability, identity issuance or queue ownership moves into the Worker.

The serializer admits only complete JSON within the character budget, using bounded prefix search when necessary. Escaping, wrappers, separators and Coding group headers count toward the relevant context budget. A budget too small for safe framing omits the block. This is structural separation, not model immunity or productive evidence. Legacy Memory write fixtures now supply explicit single-scope synthetic append grants; productive deny-by-default behavior stays in place. The focused regression passes 179 tests with two skips in 6.56 seconds, and CLI/Meet packaging detectors pass. SRP/DIP are protected by a separate pure Worker renderer; existing prompt facades retain control-formatting debt. Remaining handoff consumers, quarantine/cache enforcement and productive acceptance remain open. See `artifacts/test-gates/worker-untrusted-context-data.v1.json`.

### Memory Tree quarantine and derived-summary admission (2026-10-13)

MemoryQuarantineService delegates quarantine, revoke and release through a small scoped SQL port. Current Hub update grants and a bounded reason code are required. The adapter commits the lifecycle change and a content-free MemoryQuarantineEvent in one transaction. Repeated requests for the existing blocked state are idempotent; generic lifecycle writes cannot release blocked state, and revoked entries cannot be restored through quarantine release. Caller admission cannot supply a quarantine release record. The additive event table participates in existing SQLModel init_db/create_all; this was exercised only in isolated SQLite, with no productive deployment.

Fresh chunk reads exclude unavailable or unknown lifecycles. Summary reads check the complete bounded set of stored parent IDs and lifecycles within the tenant, without exposing child content through a wider summary grant. A missing or blocked parent hides the summary and prevents new derivation. Controlled quarantine release restores the prior admitted lifecycle; other sources remain readable. A test that previously expected a summary after its parent was deleted now checks the preserved SQL summary and denied retrieval separately.

The relevant regression passes 128 tests in 38.24 seconds, including actual retrieval, foreign namespaces, missing grants, idempotence and policy revocation before commit. SRP/DIP are protected by the narrow command port and shared lifecycle admission; the existing broad store remains CRUD/transaction debt. This does not invalidate previously delivered objects, caches, vector indexes or exports. Result-memory quarantine, final prompt revalidation, operator inspection/API and productive acceptance remain open. See `artifacts/test-gates/memory-tree-quarantine.v1.json`.
