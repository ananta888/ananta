# Harness-Evolution: Betrieb und Erweiterung

Dieses Runbook beschreibt den implementierten Stand, einschließlich offener
Produktionsintegration. Das Integrations-Mapping in
[self-evolving-harness-inventory.md](self-evolving-harness-inventory.md) bleibt
verbindlich. Planung folgt [planning-pipeline.md](planning-pipeline.md).

## Verantwortlichkeiten und aktuelle Grenzen

Der Hub besitzt Queue, Budgets, Engine-Auswahl, Quellenzulassung und Freigabe.
Worker führen ausschließlich geschlossene Aufträge aus. Eine Engine darf weder
andere Engines starten noch Ergebnisse selbst promoten. Native Prompt-Evolution
ändert Planning-Prompts; Harness-Mutation verändert explizit erlaubte Genome-
Strategien. Evaluatoren, Holdouts, Gates, Auth, Audit und Promotion sind unveränderlich.

`EvolutionProviderRegistry` ist die gemeinsame Engine-Registry. Der additive
Auswahlpfad prüft Fähigkeiten, exakte Modellrevision, Taskklasse, Mutationsflächen
und Hub-konfigurierte Ressourcenhüllen. Native-Harness- und DSPy-Proposal-Adapter
werden nur im Hub und bei `evolution.harness_engines_enabled: true` registriert.
DSPy verwendet den vorhandenen OptimizationSpec/Worker; dieser Adapter führt
keine Optimierung aus. GEPA ist noch kein zugelassener Optimizer dieses Vertrags.
Die Auswahl reserviert keine Ressourcen und ist kein Multi-Engine-Scheduler.

`POST /evolution/providers/select` benötigt die vorhandene Authentifizierung.
`evolution.selection_envelopes` ist Hub-Konfiguration, kein Provider-Ergebnis.
Pro Provider sind `enabled`, `engine_version`, `model_revisions` (Modell auf Revisionen),
`task_classes`, `mutation_surfaces`, `maximum_cost`, `maximum_model_calls` und
`maximum_wall_seconds` erforderlich. Ohne explizite Hülle ist die Engine deaktiviert.
Diese Deklarationen müssen beim späteren Dispatch tatsächlich gemessen und
durchgesetzt werden; sie sind noch kein produktiver Kostenbeleg.

Candidate-Bindings ergänzen den bestehenden EvolutionProposal-Vertrag.
Modell-, Dataset-, Benchmark- und Parent-Bindungen werden content-addressed
gespeichert. Providerbehauptungen erhalten weder Evidenzstatus noch Rechte.
Nur der Hub Evidence Registry darf SRC_/RUN_-Identitäten ausstellen. Eine lokale
Analyse-ID, ein Git-Hash oder ein Asset-Digest ersetzt diese Identitäten nicht.

## Auswahl, Vergleich und Fehlersuche

Adminpflichtige Read-APIs unter `/api/harness`:

- `/searches?tenant_id=…&project_id=…`: begrenzte, redigierte Sessionliste.
- `/searches/<id>` und `/searches/<id>/lineage`: Vergleich/Pareto und Parent-Bezüge.
- `/assets?tenant_id=…&project_id=…`: Asset-Metadaten und Lifecycle ohne Rohpayload.
- `POST /route-preview`: zertifizierte Auswahl ohne Aktivierung oder Tool-Ausführung.

Routing bindet Tenant/Projekt, Provider/Modellrevision, Taskklasse, Hardware,
Tool-Allowlist, Kontext und Kosten/Latenz. Zertifizierte Einträge werden im
bestehenden Genome-Store als immutable `model_requirements.certified_route`
gespeichert. Ihr Manifest muss als Hub-zugelassene Quelle im exakt gebundenen,
erfolgreich verifizierten Run enthalten sein. Test/synthetische Evidenz genügt
nicht. Ohne passende zertifizierte Baseline ist das Ergebnis `blocked`; eine
Modellrevision übernimmt niemals die Zertifizierung ihrer Vorgängerversion.
Der produktive Agentloop verwendet diesen Router noch nicht automatisch.

Benchmark-Suiten sind content-addressed und enthalten eingefrorene Holdouts.
Neue Kandidaten einschließlich Crossover müssen erneut evaluiert werden.
Gate-Fehler disqualifizieren unabhängig von Fitness; Pareto hält Qualität,
Generalisierung und Ressourcen getrennt. Action Fusion, ObservationPack,
evidenzerhaltende Reduction und Online-Compaction sind Harness-Strategien,
keine Berechtigungen. Ihre produktive Integration und reale Kostenmessung sind
noch Gegenstand offener Tasks.

## Kill-Switch, Budgetvorfall und Recovery

1. Neue Evolution-Analysen über die bestehende Hub-Konfiguration mit
   `evolution.enabled: false` stoppen. `harness_engines_enabled: false` verhindert
   Registrierung beim nächsten Start; bestehende Registry-Einträge werden dadurch
   nicht nachträglich entladen. Engine-Hüllen auf `enabled: false` setzen und
   Hub-Konfiguration neu laden beziehungsweise den Hub kontrolliert neu starten.
2. Bereits gestartete Worker-Aufträge über die bestehenden Hub-Task-/Job-
   Cancellationpfade beenden. Ein Kill-Switch hebt keine laufende Lease auf;
   deren Abschluss/Expiry im Task-System prüfen. Kein Warten auf interaktive Freigabe.
3. Bei Budgetüberschreitung neue Dispatches sperren, Task-/Lease-Bezüge und
   gemessenen Verbrauch sichern. Fehlende Messwerte sind unbekannt, niemals null.
   Unsichere oder verspätete Ergebnisse nicht erneut ausführen oder promoten.
4. Rollback erfolgt ausschließlich über die bestehende, domänenspezifische Hub-
   Promotion-/Rollback-Autorität auf einen vorher verifizierten Snapshot.
   `PromotionController` liefert eine technische Empfehlung; er aktiviert keine
   produktive Runtime. Bis zur Queue-/Release-Integration bleibt Harness-Promotion
   gesperrt. Niemals direkt ein Genom oder Provider-Asset zum aktiven Profil erklären.
5. Bei beschädigten Experimentdaten betroffenen Scope sperren, Backup und Audit
   sichern, kanonische Digests/Schema/Parent-Bezüge prüfen und aus einer verifizierten
   Sicherung wiederherstellen. Immutable Inhalte nicht nachträglich reparieren.
   Fehlende Asset-Lifecycle-Zeilen oder Digest-Konflikte führen zum Fehler.

Provider-Assets werden append-only im Hub gespeichert. Deaktivierung, Quarantäne
und Retirement nutzen Compare-and-swap-Lifecycle-Revisionen; aktive Freigabe ist
kein unterstützter Übergang. Retired ist terminal. Historie bleibt erhalten.
Run-/Asset-Persistenz verwendet derzeit getrennte Transaktionen; ein Storagefehler
kann einen unvollständigen Analyseimport hinterlassen. Wiederholung dedupliziert
Assets nach Digest, ersetzt aber keine atomare Release-Transaktion.

## EvoMap-Ownership, Kompatibilität und Migration

Der Hub besitzt Analyse-Runs, Scope, Asset-Snapshots, Audit und Freigabeentscheidungen.
EvoMap besitzt nur sein isoliertes, pro Analyse temporäres Arbeitsverzeichnis.
Gene/Capsule/Event/Prompt werden in neutrale Strategy/Experience/Event/Prompt-
Assets übersetzt; Upstream-IDs und bounded Parent-/Source-/Risk-/Mutation-Felder
bleiben im Payload als unverifizierte Providerreferenzen erhalten. Kein zweiter
Memory Graph ist kanonische Wahrheit. Die automatisierte engine-übergreifende
Outcome-/Lineage-Spiegelung und zentrale Retention sind noch nicht implementiert.

Strukturierte Ausgabe ist an Runtime 1.94.0 und GEP-Schema 1.12.1, mitgelieferte
Gene-Schemas 1.6.0/1.8.0 beziehungsweise die explizite Legacy-Variante gebunden.
Neue Schemas, ID-Konflikte, Symlinks und
übermäßige Payloads scheitern geschlossen. Mitgelieferte Basis-Gene sind keine
frisch erzeugten Vorschläge. stdout-only ist als `degraded_stdout_compatibility`
markiert. Die Docker-Bridge wird aus dem gepinnten Source gebaut; lokale Container-
Tests sind technische Beobachtungen, keine registrierte Release-Evidenz.

Alte EvolutionProposal-Rohdaten bleiben lesbar. Sie werden nicht automatisch als
Gene/Capsule reinterpretiert. Eine spätere Migration muss Runtime/Schema und
Task-Scope aus verifizierter Herkunft bestimmen, dieselbe Asset-Validierung
verwenden und ihre Herkunft auditieren. Fehlen diese Angaben, bleiben die alten
Daten unverifizierte Legacy-Proposals. Keine künstlichen SRC_/RUN_-IDs erzeugen.

Kindprozess-Umgebung enthält keine geerbten Hub-Credentials. Loopback-URLs sind
keine Netzwerk-Isolation. Produktive Egress-Policy und Memory-Retention bleiben
offen; vor einem externen Evolver-Einsatz müssen sie separat zugelassen sein.

## Einen Policy-Typ ergänzen

1. Bestehenden Strategie-Port in `harness_runtime.py` und den Genome-Vertrag
   prüfen. Eine fokussierte Implementierung im Registry-Erweiterungspunkt
   registrieren; den Agent-Kernloop nicht um typabhängige Fallunterscheidungen erweitern.
2. Neue Felder optional und migrationsfähig ergänzen. Kanonisierung, Diff und
   Modell-/Tool-/Context-Capability-Prüfung definieren. Secret-/Gate-Referenzen
   und Evidenzpflicht dürfen nicht mutiert werden.
3. Eine pure Mutation über `MutationEngine.operators` registrieren, mit explizitem
   Budget, erlaubter Surface, Parent und Begründung. Die Mutation erzeugt nur
   einen Kandidaten. Code-Patches benötigen zusätzlich zugelassene Module.
4. Als Engine den bestehenden EvolutionEngine-SPI verwenden, Version und echte
   Capabilities deklarieren. Provider-Metadaten sind keine Policy. Kandidaten über
   den vorhandenen Normalizer, isolierten Worker und die gemeinsame eingefrorene
   Suite prüfen; keine eigene Queue oder Promotion hinzufügen.
5. Headless-Regressionen für Rechteausweitung, Modellrevision, Deadline,
   Invarianten, Storagefehler und Fail-closed-Verhalten schreiben. Benchmarks mit
   synthetischen Inputs entsprechend klassifizieren. Produktive Freigabe benötigt
   separat vorab Hub-reservierte und anschließend exakt verifizierte Evidenz.

SRP/DIP: Router, Zertifikatsprüfung, Katalog, Audit, Provider-Mapping und Asset-
Persistenz haben getrennte Ports beziehungsweise Module. Erhaltene technische
Schuld: EvolutionRunService koordiniert mehrere Repositorytransaktionen;
atomare Imports und eine schmalere Ingestion-Abstraktion sind weiter erforderlich.
