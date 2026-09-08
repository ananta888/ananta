# Task- und raumgebundene Meet-Vorautorisierung

Die zusätzliche Grenze ist standardmäßig aus:
`ANANTA_MEET_DIALOG_PREAUTHORIZATION_ENABLED=0`.
Sie wird im Hub konfiguriert, nicht im Publisher. Sie ersetzt weder
`ANANTA_MEET_DIALOG_POLICIES` noch Projekt-/Taskrechte, aktive Organisationsrollen,
das öffentliche Meet-Trustprofil oder die getrennten Empfangsfreigaben.

## Vorbereiten und automatisch provisionieren

Der lokale Operator arbeitet im konfigurierten Hub-Container beziehungsweise
Hub-Environment (`ROLE=hub`) mit dessen Datenbankkonfiguration. Das CLI ist ein
privilegiertes lokales Verwaltungstool für Inhaber dieses Datenbankzugriffs,
kein Worker-Tool und kein durch bloßes Setzen von `ROLE` ersetzter Auth-Endpunkt.
Es startet keine Hintergrunddienste, erzeugt keine Meeting-Mitgliedschaft und
kontaktiert keinen Worker. Keine interaktive Bestätigung ist erforderlich.

Die JSON-Datei muss ein eigenes reguläres, nicht verlinktes File mit privatem
Modus `0600` oder `0400` sein (höchstens 4096 Bytes; absoluter Pfad ohne Symlinks).
Sie enthält exakt:

| Feld | Bedeutung |
| --- | --- |
| `schema` | `ananta.meet-dialog-preauthorization-policy.v1` |
| `policy_id` | Eindeutiger Operator-Policyname, keine Evidence-ID |
| `tenant_id`, `project_id` | Ein exaktes vorhandenes Projekt |
| `parent_task_id` | Ein exakter aktiver Hub-Task; kein leerer Projekt-Wildcard |
| `owner_subject` | Exakter für den Start berechtigter Benutzer-/Automationsprincipal |
| `origin`, `room_id` | Kanonischer HTTPS-Meet-Origin und vorhandene exakte Raumzuordnung |
| `capabilities` | Nichtleere Auswahl bestehender Maschinenfähigkeiten; keine Tools |
| `valid_from`, `expires_at` | Ganzzahlige Unix-Sekunden; Zeitfenster 30 Sekunden bis 30 Tage |
| `max_duration_seconds` | Obergrenze eines Auftrags: 30 bis 7200 Sekunden |
| `max_dispatches` | Obergrenze reservierter Starts dieser Revision: 1 bis 1000 |

Alle Scope-Werte und Zeiten kommen aus der ausdrücklich vorautorisierten
Operator-Konfiguration; das CLI errät sie nicht aus einem Todo oder Raumcode.
Den Raum vorher über die bestehenden berechtigten Hub-Binding-/Allocation-APIs
zuordnen. Schlüssel können mit dem vorhandenen Schlüssel-Provisionierer
vorbereitet werden; Meet erhält nur öffentlichen Trust, nie den privaten Hub-Key.

```bash
python -m scripts.meet_dialog_preauthorization provision \
  --policy-file /run/secrets/meet-dialog-policy.json --expected-revision 0
```

Erst danach bei Bedarf die zusätzliche Hub-Grenze explizit aktivieren.
Neu gestartete Tasks erhalten die unveränderliche Policy-/Revisionsbindung;
alte Tasks werden nicht nachträglich ausgestattet. Ein bereits gebundener
Task darf nach Deaktivierung des Providers nicht auf alte Projektpolicy
zurückfallen. Fehlende Policy oder Infrastruktur liefert begrenzte Fehler,
keine Aufforderung zu einem menschlichen Login.

## Aktualisieren und widerrufen

Ein Update derselben exakten Policy verwendet deren aktuelle Revision als
`--expected-revision`; es erzeugt die nächste Revision und ein neues Startbudget.
Alte Aufträge bleiben an die alte Revision gebunden und verlieren ihre Freigabe.
Eine neue Scope-Zuordnung benötigt einen eigenen Policynamen. Nach ungewissem
Ausgang niemals blind die Revisionsnummer erhöhen oder erneut starten.

```bash
python -m scripts.meet_dialog_preauthorization revoke \
  --policy-id EXAKTER_POLICYNAME --expected-revision AKTUELLE_REVISION
```

Der Widerruf invalidiert weitere Hub-Autoritätsprüfungen, einschließlich
Erneuerung und Ausgabe. Der bestehende Worker-Frischewächter schließt seine
Quellen; der separate Task-Not-Aus bleibt bedienbar. Widerruf ist keine Löschung
von Schlüsseln, Aufträgen oder früherer Historie.

Ein reservierter Start bleibt auch bei Fehler vor Task-Ingestion oder nach
ungewissem Dispatch verbraucht. Es gibt keine automatische Rückerstattung,
Wiederholung oder Freigabe durch einen Worker. SQL-Locks und CAS schützen das
Startbudget zwischen mehreren Hub-Prozessen; dies ist keine HA-Abnahme.

Das CLI liefert nur feste JSON-Zustände, Policynamen, Revision und Dokumenthash
oder `blocked` mit Exit 2. Datenbankfehler und Eingabepfade werden nicht gedumpt.
Der SQL-Audit enthält OS-Operatoridentität (`local-uid:<uid>`), Revision, Status,
Zeitpunkt und Dokumenthash, aber keine Raum-Invites oder Mediendaten.

## Verifikation und Grenzen

171 gezielte Tests bestanden in 119,11 Sekunden: geschlossener Policyvertrag,
echtes SQL einschließlich konkurrierender Starts, Neustart und Widerruf,
originale Taskbindung, unveränderte v1-Workerhülle, idempotente Startkoordinierung,
Terminalschutz und begrenztes Operator-CLI. Ein echter CLI-Prozesslauf verwendet
ausschließlich ein eigenes temporäres Datenbankfile und private synthetische
Policydateien; die Worker-Rolle darf dieses File nicht einmal anlegen.

Erster Hub-Testversuch: eine im Test gemeinsam wiederverwendete Hilfstabelle
kontaminierte acht Folgeprüfungen. Die Policy-Fixture besitzt jetzt eine eigene
SQL-Datei je Fall; Hub-Task-, Rollen- und Projektprüfungen bleiben real. Danach
bestanden alle 23 Hub-/CLI-Fälle in 26,12 Sekunden.

Die separaten Container-/Empfängerprüfungen und ihr vorab festgelegtes
Fünf-Sekunden-End-to-End-Widerrufsbudget sind in
`docs/contracts/meet-dialog-preauthorization.md` dokumentiert.
Kein öffentliches Trustprofil, keine produktive Projektpolicy und kein
Serving-Build wurde durch die Implementierung aktiviert. Lokale Testbeobachtungen
sind keine Hub-Registry-Produktionsfreigabe; öffentliche TURN-/Soak-Gates bleiben
ein eigener Arbeitsschritt.
