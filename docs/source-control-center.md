# Source Control Center

Das Source Control Center verwaltet Quellen, Revisionen, Indizes und ihre
Zugriffsregeln. Die Bedienoberflaeche zeigt Entscheidungen des Hubs; sie trifft
selbst keine Sicherheitsentscheidung.

## Begriffe

**Connection** beschreibt eine registrierte Quelle, etwa einen Workspace, ein
GitHub-Repository oder einen Wiki-Import. Credentials werden nur als
serverseitige Referenz gespeichert und nie an den Browser zurueckgegeben.

**Revision** ist ein unveraenderlicher Stand der Connection. Bei Git ist dies
ein Commit, bei einem Workspace ein Digest des begrenzten, sortierten
Dateimanifests. Refresh erzeugt bei einer Aenderung eine neue Revision und
veraendert keine alte.

**Admission** ist die Hub-Entscheidung nach Inventar und Scan. Secrets,
Prompt-Injection, nicht erlaubte Dateitypen, Archive, Symlinks, Hardlinks,
Sparse Files oder ueberschrittene Budgets koennen eine Revision blockieren.

**Knowledge Index** ist ein aus genau einer Revision und einem
Policy-Snapshot erzeugter Index. Nur ein vollstaendig verifizierter Index kann
als **Active Index** aktiviert werden.

**Stale** bedeutet, dass Connection-Revision, aktiver Index oder Policy nicht
mehr denselben Stand repraesentieren. Stale ist keine automatische Freigabe
zum Reindexieren; Admission und Policy werden erneut geprueft.

## Lokale und Cloud-Ziele

Ein Ziel bindet Worker, Runtime, Provider, Modell, Modellklasse, Standort und
Datenresidenz. Zwei Claude-Modelle sind daher zwei unterschiedliche Ziele.
Lokale und Cloud-Grants sind getrennt. Ein lokaler Grant kann nicht fuer ein
Cloud-Modell wiederverwendet werden.

Vor einem Grant zeigt der Hub eine Preview mit Operation und Transformation:

- `raw`: unveraenderter zugelassener Inhalt
- `redacted`: serverseitig redigierter Inhalt
- `summary`: nur eine freigegebene Zusammenfassung

Ein Ziel-, Modell-, Revisions- oder Policy-Wechsel nach der Preview blockiert
den Dispatch.

## Credential-Widerruf

Wird eine GitHub-Installation, ein OAuth-Scope oder eine Secret-Referenz
widerrufen, wechselt die Connection zu `authorization_required`. Refresh und
Indexierung bleiben blockiert. Bereits gespeicherte immutable Revisionen werden
nicht umgeschrieben und erhalten dadurch keine neue Freigabe.

## Aktivierung und Rollback

Indexabschluss und Aktivierung sind getrennt. Aktivierung verwendet einen
versionsgeschuetzten Active-Zeiger. Rollback aktiviert einen frueheren,
vollstaendig verifizierten Index atomar; historische Artefakte werden dabei
nicht veraendert.

## Disable, Tombstone und Purge

`Disable` stoppt neue Operationen fuer eine Connection.

`Tombstone` markiert einen nicht aktiven Index zur spaeteren Entfernung.

`Purge` entfernt physische Daten erst nach Pruefung von Active-Zeiger, Leases,
Grants, Citations und Approvals. Sensitive Daten erfordern eine explizite
Freigabe. Diese drei Schritte sind getrennt und werden content-frei auditiert.

## Fehlerzustand

Die Oberflaeche unterscheidet Offline, Authentifizierung, fehlende
Berechtigung, nicht gefunden, Versionskonflikt, Validierungsfehler, Rate Limit
und Serverfehler. Ein Fehler erzeugt niemals eine lokale Ersatzfreigabe.

## Gefuehrter Projektpfad und lokale Indexfreigabe

Der Standardpfad beginnt im sichtbaren Hauptmenue `Quellen & Indexierung`.
Nach der Projektauswahl fuehrt der CTA `Git oder Ordner hinzufügen` in eine
projektgebundene Journey. Der dauerhafte Hinweis
`Wird Projekt <Projektname> zugeordnet` macht sichtbar, dass jede neu
angelegte Connection genau diesem Projekt gehoert.

Die Journey trennt die Quellenarten in vier klare Karten:

- `Öffentliches Git/GitHub-Repository`
- `Lokaler Ordner / lokale Git-Arbeitskopie`
- `Server-Workspace`
- `Privates GitHub`

Lokale Ordner werden als begrenzter Browser-Snapshot uebertragen; VCS- und
Ananta-Metadaten sowie absolute Pfade sind ausgeschlossen. Server-Workspaces
verwenden ausschliesslich Hub-gelieferte IDs. Privates GitHub zeigt den realen
Providerstatus und bleibt ohne serverseitige Autorisierung fail-closed.

Nach Refresh und erfolgreichem Scan bereitet ausschliesslich der Hub die
einmalige lokale Indexfreigabe vor:

```text
GET  /api/source-control/v1/connections/<connection_id>/actions/prepare-index-access?project_id=<project_id>
POST /api/source-control/v1/connections/<connection_id>/actions/prepare-index-access?project_id=<project_id>
```

GET liefert die autoritative Revision, lokale Container-Destinationen und nur
serverseitig erlaubte Optionen. Der aktuell sichere Vertrag bindet das Ziel an
`provider_location=local_container` und `data_residency=local`; die Wirkung
bleibt exakt `provider_location=local`, `transformation=redacted` und
`one_time=true`. POST akzeptiert nur eine servergelieferte Destination und
Option, eine begrenzte Dauer sowie `confirmed=true`. `If-Match` bindet die
Vorbereitung per OCC, `Idempotency-Key` macht Wiederholung stabil.

Stale ETags, fehlende Bestaetigung, erfundene Ziele oder Optionen, ungueltige
Dauern und nicht lokale beziehungsweise nicht redigierte Wirkungen werden
abgewiesen. Der Browser ruft weder den Policy-Lifecycle noch die Grant-Admin-
API als eigene Sequenz auf; Preview, Aktivierung und einmaliger Grant werden
atomar durch den Hub-Aggregatbefehl komponiert.

## Aktueller Capability-Stand

Die folgende Matrix beschreibt den implementierten und lokal verifizierten
Stand. Sie ist kein Produktionsnachweis. Zusaetzlich zu den bestehenden Gates
liefen die initialen fokussierten Angular-Vertraege mit `32/32`; nach Abgleich
der finalen Aggregate-Normalform bestanden `6/6` fokussierte Tests sowie App-
und Spec-Typecheck. Die lokalen Chromium-Journeys fuer Public Git und
Workspace-Snapshot bestanden jeweils `1/1` ohne Retry bis zur Aktivierung.

| Capability | Backend | Angular | Externe private Provider | Verifikation |
|---|---|---|---|---|
| Atomare Connection-Bindung | Implementiert; Connector-Auswahl und validierter `relative_path` werden gemeinsam gebunden. | Der bestehende Quellen-Assistent verwendet die Connection-API. | Nicht erforderlich. | Backend-Gate `167/167`; Planning-Gates gruen. |
| Git-Authorization-Lifecycle | Persistente List-, Detail-, Health-, Revoke- und Scope-loss-Flows sind implementiert. | Git-Authorization-UI ist implementiert. | Fuer private GitHub-App-/OAuth-Nutzung weiterhin erforderlich und nicht konfiguriert. | Auth-Gate `147/147`; Angular `68/68`, Build und Project-Selector `42/42`. |
| Credential-freies Public Git/GitHub | Strukturierter Validate/Create-Pfad ohne Credential oder Secret sowie Hub-owned Prepare-Index-Access sind implementiert. | Der sichtbare Public-Remote-Flow ist lokal bis zur Aktivierung verifiziert. | Nicht erforderlich. | Chromium `1/1` ohne Retry: Projekt-CTA, Public-Git-Karte, Validate, Create, Connection, Refresh, Scan, lokale redigierte Einmalfreigabe, Run, Hub-Worker-Ausfuehrung und Aktivierung bestanden. |
| Lokale Workspace-Folder-Registration | Opaque Folder-Handles, TTL-Validation, read-only Registrierung, Catalog-Aufloesung, CAS-Disable und Snapshot-Ausschluesse sind implementiert. | Die sichtbare Workspace-Snapshot-Journey ist lokal bis zur Aktivierung verifiziert. | Nicht erforderlich. | Chromium `1/1` ohne Retry: Projekt-CTA, Snapshot, Connection, Refresh, Scan, Hub-Aggregatfreigabe, Index-Run, Worker-Ausgabe, Projektion, Aktivierung und UI-Readback bestanden. |
| Private GitHub App/OAuth | Fail-closed Provider- und Secret-Ports sowie server-handle-only API sind implementiert. | Authorization-Auswahl und Lifecycle sind bedienbar. | Reale App/OAuth-Registration, Installation und Grant fehlen. | Extern `unverified`. |

## Connection-Bindung und relative Pfade

Eine Workspace-Connection nimmt keinen Hostpfad aus dem Browser an. Der Hub
loest die serverseitige `workspace_id` auf, validiert den optionalen
`relative_path` innerhalb dieser Registrierung und persistiert beides in einer
atomaren Connection-Bindung. Eine teilweise gespeicherte Auswahl darf nicht
als verwendbare Connection sichtbar werden.

## Sichere Workspace-Folder-Registration

Die Folder-Navigation verwendet kurzlebige opaque Handles unter dem
projektgebundenen `ANANTA_WORKSPACE_ROOT`. Browserantworten enthalten weder
Hostpfade noch Dateinamen. Validate bindet Root-Generation, Verzeichnisidentitaet
und bounded Manifest an ein kurzlebiges SQL-Handle. Create verbraucht dieses
Handle einmalig und erzeugt eine opaque, read-only `workspace_id`. Detail und
Disable sind Scope-/RBAC-gebunden; Disable verwendet ETag/`If-Match`, CAS,
Idempotenz und content-freies Audit.

Der registrierte Workspace erscheint im bestehenden Workspace-Catalog. Ein
Source-Intent referenziert weiterhin nur `workspace_id` und den validierten
relativen Pfad, niemals einen Hostpfad.

## Credential-freie Public Remotes

Public Git/GitHub verwendet einen separaten Validate/Create-Pfad mit
strukturierten Selektoren und ohne Credentials. Lokale Compose-Nutzung ist ein
explizites Opt-in. In Produktion bleibt
`ANANTA_SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED` standardmaessig deaktiviert.
Die lokale Chromium-Journey validierte und erstellte
`octocat/Hello-World@master`, band den aufgeloesten immutable Commit an eine
Connection und durchlief Refresh, Scan, Hub-owned lokale Einmalfreigabe,
Index-Run, Worker-Ausfuehrung und Aktivierung. Sie bestand `1/1` ohne Retry.
Eine private GitHub-App-/OAuth-Installation wurde dabei bewusst nicht
verwendet und bleibt eine externe Voraussetzung.

## Worker-Ausgaben und Hub-Projektion

Der Hub delegiert den Index-Auftrag und bleibt Eigentuemer von Job, Lease,
Policy-Entscheidung und Projektion. Worker-Ausgaben werden mit einer
kurzlebigen signierten Capability gelesen, die an Job, Assignment, Lease und
Artefakt gebunden ist. Weder Nutzer- noch Hub-Bearer werden dafuer an einen
Worker weitergereicht. Der Hub prueft Artefaktgroesse, Medientyp und Digest
und materialisiert danach die kanonischen Index- und Run-Bindungen atomar und
idempotent. Die Aktivierung bleibt ein separater CAS-geschuetzter Schritt.

## Ausgefuehrter lokaler Integrationsstand

Das Live-PostgreSQL-Upgrade erreichte den Alembic-Head mit Praefix `5c0f...`.
Hub und Angular waren danach healthy. Ein realer claimloser
Admin-plus-`project_id`-Smoke lieferte fuer Workspace-Registration List `200`,
Validate `200`, Create `201`, den passenden Workspace im Catalog und Disable
`200`. Angular `/sources/add` lieferte `200`.

Darauf aufbauend bestanden zwei aktuelle Chromium-Journeys jeweils `1/1` ohne
Retry bis zur Aktivierung. Public Git und Workspace-Snapshot nutzten den
sichtbaren Hauptmenue-, Projekt-CTA- und Kartenpfad. Beide durchliefen
Connection, Refresh, Scan, den einzigen Hub-owned Prepare-Index-Access-
GET/POST, Index-Run, Claim mit `900` Sekunden Lease, Propose/Execute,
Hub-Projektion und explizite Aktivierung. Der Browser orchestrierte dabei
weder Policy-Lifecycle noch Grant-Administration. Graph und Query antworteten
nach dem Authorization-Whitelist-Fix jeweils mit HTTP `200`.

Diese Resultate belegen die lokale Compose-Integration, nicht eine private
GitHub-App-/OAuth-Konfiguration, Cloud-/Claude-Nutzung oder Produktion. Die
deterministische Evidence liegt unter
`artifacts/test-gates/source-control-center-live-browser-verification.json`.
