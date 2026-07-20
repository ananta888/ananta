# Betrieb: Semantic Media und Speech

## Sicherheitsinvarianten

Der Hub bleibt Control Plane, Besitzer von Membership, Consent, Epoch, Contract,
Lease, Task und Freigabe. SFU, Browser und Worker führen nur autorisierte Arbeit
aus. Ordinary WebRTC bleibt unabhängig verfügbar. Kein Debug-, SFU- oder
Workerpfad darf Hub-Entscheidungen treffen.

## Deploy und Migration

1. Featureflags auf `observe_only` und Semantic/Training-Aktivierung auf `false`
   halten. Ordinary-Fallback und Stop/Revoke zuerst verifizieren.
2. `alembic heads` muss exakt einen erwarteten Head liefern; anschließend
   `alembic upgrade head`. Bei mehreren Heads nicht mergen oder fortfahren,
   sondern die konkurrierende Migration klären.
3. digestgepinnte Images und Konfiguration deployen. Secrets nur über den
   vorgesehenen Secretprovider zuführen; niemals in Compose, Log oder SBOM.
4. Hub, Relay, SFU, Reconciliation- und Training-Worker einzeln auf Readiness,
   Clock/Epoch und Queue-/Lease-Fencing prüfen.
5. `python scripts/run_semantic_media_program_release_gate.py` ausführen. Ein
   `NO-GO` ist der sichere Normalzustand, solange externe Evidenz fehlt.

## Health und Capacity

Beobachtet werden ausschließlich content-free Counts und Latenzen: aktive
Contracts/Leases, Epoch, Queuebytes je Traffic-Klasse, p50/p95/p99,
Reconnect-/Fallback-/Rekey-Counts, Quarantäne-/Cleanup-Counts, CPU/GPU,
RAM/VRAM, Disk, Energie und Budget. Transcript-, Feature- oder Medieninhalte
gehören nie in Telemetrie.

Stopkriterien sind E2EE-Downgrade, Auth-/Consentfehler, Leakage-Canary,
Live-p95/p99 über Gate, steigende unresolved Revocations, unbounded Ressourcen,
Lease-Duplikate oder Budgetüberschreitung. Ordinary Calls werden dabei nicht
beendet.

Der produktive Browserpfad speist den Speech-Qualitätscontroller aus der
begrenzten Semantic-Sendewarteschlange, WebRTC-Paketverlustfenstern,
Partialalter, ausstehenden Korrekturen, Source-/Featureverlust und den
Qualitätsergebnissen des receiver-lokalen Reconstructors. Die Facade hält den
verschlüsselten Ordinary-Audiopfad bereits beim Start der semantischen Sprache
bereit. Ein Qualitäts-Fallback stoppt synthetische Wiedergabe und verzögerte
Source-Verarbeitung, lässt das autoritative Transcript aber sichtbar. 404 und
409 purgen die semantische Sitzung; 413 verwirft das betroffene Segment ohne
Retry und reduziert die folgende Segmentdauer. Alle drei Fälle behalten den
Ordinary-Pfad und einen stabilen content-free Reason-Code.

## Angular-Autorisierungsprojektion

`authoritatively_active` darf die integrierte Oberfläche nicht aus einem
lokalen Featureflag ableiten. Für Offline-Sprachabstimmung kombiniert die
Facade eine erfolgreiche, authentifizierte Hub-Liste mit der bereits vom Hub
gelesenen aktuellen Consent-Projektion. Diese muss Session und Security-Epoch,
Owner/Tenant, Zweck `speech_reconciliation`, Datenklasse `audio` sowie die
Einzelfreigaben für Roh-Audio und Dataset-Import binden. Training bleibt eine
separate Freigabe und wird nicht implizit aktiviert. Consentwechsel, Ablauf,
Session-/Epochwechsel und Offlinezustand entziehen die Panel-Autorisierung und
fencen eine noch ausstehende Read-Antwort als stale. Der capability-spezifische
Consent-Umfang wird vor der Aktivierung sichtbar dargestellt; ein statisches
`hubAuthorized=true` ist unzulässig.

## Drain, Pause und Kill-Switch

1. Neue Semantic Contracts, Evidence-Offers und Training-Jobs sperren.
2. Bulk lane pausieren; Control, Revoke und Live-Transcript priorisiert lassen.
3. Aktive Leases bounded auslaufen lassen oder am Hub canceln und Attempt
   erhöhen. Späte Workerresultate verwerfen.
4. SFU Publisher drainen, Ordinary Tracks nicht trennen.
5. Semantic-, Evidence-, Offline- und Adapterflags deaktivieren. `observe_only`
   bleibt optional für content-free Diagnose aktiv.

## Rekey, Revoke und Keyrotation

Membershipänderungen erhöhen die Epoch. Entfernte Teilnehmer verlieren neue,
Late Joiner erhalten keine alten Schlüssel. Bei Keyverdacht: Offer/Lease fence,
lokale Keys vernichten, Epoch erhöhen, bestätigte Teilnehmer neu binden und erst
danach Semantic fortsetzen. Speech-Revoke fencet Capture/Transfer/Jobs/Adapter
lokal vor Remote-Ack; der Peer bleibt `unresolved`, bis ein gültiges Ack vorliegt.

## Worker-Cancel und Cleanup

Cancel wird ausschließlich vom Hub erzeugt. Worker dürfen keine Nachfolgejobs
starten. Attempt-, Lease-, Consent- und Epoch-Fence gelten am Commit. Temporäre
Dateien, Timer, Tracks und Reservations werden idempotent beseitigt; Retention
vernichtet zuerst Schlüssel und löscht danach Chiffretext, während ein
content-free Tombstone Wiederimport verhindert.

## Rollback

Zurückrollen heißt Flags deaktivieren, neue Verträge stoppen, Bulk drainen,
Worker canceln, SFU aus dem Routing nehmen und Ordinary weiterführen. Additive
DB-Spalten/Tabellen bleiben bis zur nachgelagerten, separat geprüften Migration
liegen; kein destruktiver Down-Migrate während eines Incidents. Der Rollback ist
erst abgeschlossen, wenn Ressourcen-/Lease-Counts stabil null sind und Revoke-
Status nicht fälschlich als gelöscht dargestellt wird.

## Incidentpfade

- **Verdacht auf Klartextleak:** Semantic Kill-Switch, Export stoppen, Logzugriff
  einfrieren, Keys/Epoch rotieren, Canarys scannen, Datenschutzteam informieren.
- **Gestohlener Schlüssel:** betroffene Membership fence, Epoch erhöhen, Keys
  vernichten, Replayfenster erhalten, neue Keyconfirmation erzwingen.
- **Poisoned Evidence:** Admission/Training/Adapter fence, Lineage-Impact bilden,
  Kinddataset ohne betroffene Gruppen bauen; Parent unverändert lassen.
- **Stale Adapter:** Inferenzzuordnung sofort fence, Registryversion erhöhen,
  laufende Requests auf Basismodell/Ordinary zurückfallen lassen.
- **SFU kompromittiert:** SFU aus Routing entfernen, alle Gruppenschlüssel
  rotieren, TURN/SFU-Egress auditieren; keine Payloadlesbarkeit behaupten.
- **Budget runaway:** neue Leases sperren, Worker bounded canceln, Reservations
  freigeben und Ursache content-free über Reason-Codes auswerten.
