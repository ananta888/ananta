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
