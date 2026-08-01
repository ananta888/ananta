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

## Aktueller Capability-Stand

Die folgende Matrix beschreibt den implementierten und lokal verifizierten
Stand. Sie ist kein Produktionsnachweis. Die aktuellen Backend-Gates liefen
mit `167/167`, der PostgreSQL-Identifierfix mit `14/14` und der
Auth-Produktionsfix mit `147/147`. Angular lief mit `68/68`, erfolgreichem
Build und `42/42` Project-Selector-Tests.

| Capability | Backend | Angular | Externe private Provider | Verifikation |
|---|---|---|---|---|
| Atomare Connection-Bindung | Implementiert; Connector-Auswahl und validierter `relative_path` werden gemeinsam gebunden. | Der bestehende Quellen-Assistent verwendet die Connection-API. | Nicht erforderlich. | Backend-Gate `167/167`; Planning-Gates gruen. |
| Git-Authorization-Lifecycle | Persistente List-, Detail-, Health-, Revoke- und Scope-loss-Flows sind implementiert. | Git-Authorization-UI ist implementiert. | Fuer private GitHub-App-/OAuth-Nutzung weiterhin erforderlich und nicht konfiguriert. | Auth-Gate `147/147`; Angular `68/68`, Build und Project-Selector `42/42`. |
| Credential-freies Public Git/GitHub | Strukturierter Validate/Create-Pfad ohne Browser-URL, Credential oder Secret ist implementiert. | Noch nicht als eigener Public-Remote-Flow nachgewiesen. | Nicht erforderlich. | Live-Validate `200` mit aufgeloestem 40-Zeichen-Commit; weiterhin `partial`, weil Live-Create und UI-E2E fehlen. |
| Lokale Workspace-Folder-Registration | Opaque Folder-Handles, TTL-Validation, read-only Registrierung, Catalog-Aufloesung und CAS-Disable sind implementiert. | `/sources/add` war erreichbar; eine vollstaendige Browser-Registration-Journey wurde nicht ausgefuehrt. | Nicht erforderlich. | Backend lokal verifiziert; Live-Smoke: List `200`, Validate `200`, Create `201`, Catalog-Match und Disable `200`. |
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
Live-Validate war erfolgreich und lieferte einen aufgeloesten 40-Zeichen-
Commit. Die Capability bleibt dennoch `partial`, weil kein Live-Create und
kein Public-Remote-UI-E2E ausgefuehrt wurden.

## Ausgefuehrter lokaler Integrationsstand

Das Live-PostgreSQL-Upgrade erreichte den Alembic-Head mit Praefix `5c0f...`.
Hub und Angular waren danach healthy. Ein realer claimloser
Admin-plus-`project_id`-Smoke lieferte fuer Workspace-Registration List `200`,
Validate `200`, Create `201`, den passenden Workspace im Catalog und Disable
`200`. Angular `/sources/add` lieferte `200`.

Diese Resultate belegen die lokale Backend- und Integrationsfaehigkeit. Sie
belegen keine Angular-Browser-Journey fuer Workspace-Registration, keinen
Public-Remote-Live-Create und keine private GitHub-App-/OAuth-Konfiguration.
