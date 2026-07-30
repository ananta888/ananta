# Source Control Center: Betrieb und Rollout

## Sicherheitsgrenzen

Der Hub ist die einzige Autoritaet fuer Source Admission, Policy, Grants,
Aktivierung, Rollback und Purge. Connectoren und Worker fuehren nur einen
assignment- und lease-gebundenen Auftrag aus. Sie erstellen keine Hub-Tasks und
orchestrieren keine anderen Worker.

Audit-Events enthalten ausschliesslich opaque IDs, Digests, Actor, Tenant,
Projekt, Operation, Entscheidung, Reason Code und Trace-ID. Quellinhalt,
Dateipfade, URLs, Querytext und Credentials duerfen weder Audit noch
Metriklabels erreichen.

## Metriken und Alarme

Zulaessige Metriklabels sind `connector_type`, `decision`, `operation`,
`reason_code` und `status`. Source-, Revision-, Run-, Nutzer- und Trace-IDs sind
als Labels verboten.

Zu erfassen sind:

- Queue- und Laufzeit pro Operation
- Erfolg, Abbruch und Policy-Deny
- Alter stale gewordener Sources
- Coverage und verarbeitete Bytes als Werte, nicht Labels
- Rate-Limit- und Autorisierungsfehler

Alarme muessen ausloesen bei:

- dauerhaft stale Sources
- wiederholtem Credential- oder Scope-Verlust
- blockierten oder abgelaufenen Leases
- Artefakt-Hashdrift
- Speicher- oder Retention-Grenzen
- wiederholten Policy-Denials nach einer Rollout-Aenderung

## Sichere Wiederherstellung

1. Neue Dispatches fuer die betroffene Revision stoppen.
2. Credential- und Egress-Zustand pruefen, ohne Secrets auszugeben.
3. Immutable Revision, Admission-, Manifest- und Policy-Digest vergleichen.
4. Unvollstaendige Runs reconciliieren; niemals einen Run nur anhand eines
   vorhandenen Verzeichnisses auf `completed` setzen.
5. Den letzten vollstaendig verifizierten Index atomar aktivieren.
6. Den Vorfall content-frei auditieren.
7. Physische Daten erst nach Retention-, Referenz-, Lease-, Grant-, Citation-
   und Approval-Pruefung purgen.

## Rollout-Stufen

Jede Stufe wird in einem abgeschlossenen Beobachtungsfenster mit mindestens
`100` Samples bewertet. Eine Stufe darf nur weitergeschaltet werden, wenn die
semantische Shadow-Differenzrate `<= 0,5 %` ist. Bei `>= 2,0 %` wird der
Rollout abgebrochen. Bei einer Autorisierungsfehlerrate `>= 1,0 %` oder einer
Gesamtfehlerrate `>= 2,0 %` wird auf die letzte verifizierte Stufe
zurueckgerollt. Werte zwischen Erfolg und Abbruch halten die aktuelle Stufe.
Ein Shadow-Ergebnis darf niemals die kanonische Policy-Entscheidung ersetzen.

| Stufe | Aktivierung | Erfolg | Abbruch und Rollback |
|---|---|---|---|
| Shadow Read-Model | Neue Projektion liest Legacy-Zustand ohne Runtimewirkung. | Keine semantischen Differenzen ausser dokumentierten Legacy-Luecken. | Projektion deaktivieren; Legacy bleibt autoritativ. |
| Persistente Sources | Connection und immutable Revision werden dual geschrieben. | Stabile Zuordnung und keine zusaetzliche Freigabe. | Neue Writes stoppen, idempotent reconciliieren. |
| Workspace-Indexierung | Nur registrierte Workspaces und lokale Ziele. | Admission, Lease, Manifest und Active-Zeiger konsistent. | Dispatch stoppen, vorherigen Index aktivieren. |
| Lokale Grants | Revisions- und Destination-gebundene lokale Grants. | Preview und Dispatch verwenden denselben Digest. | Grants widerrufen; Default bleibt deny. |
| GitHub | Freigegebene App-Installationen und immutable Commits. | Widerruf blockiert Refresh und Indexierung. | Connector deaktivieren; vorhandene Revisionen bleiben immutable. |
| Cloud-Grants | Konkrete Provider-, Modell- und Standortidentitaet. | Kein Zielwechsel zwischen Preview und Dispatch. | Cloud-Grants widerrufen; lokale Grants bleiben getrennt. |
| Legacy-Abschaltung | Nur nach gemessener Alias-Nutzung von null. | Alle vertikalen Gates und Produktionsverifikation bestanden. | Legacy-Adapter erneut aktivieren, keine Daten rueckmigrieren. |

## Reproduzierbares Release-Gate

`scripts/check_source_control_release_gate.py` erwartet ein Manifest mit
Schema-Version `1.0`, exakt einem SHA-256-gebundenen Ergebnis fuer Contract,
Security, Migration, Backend, Angular, E2E, Accessibility, Container und
No-Bypass, Rollout und Load/Recovery sowie einer separaten
Produktionsverifikation.

Die reproduzierbaren Definitionen liegen in
`artifacts/test-gates/source-control-container-smoke-definition.json` und
`artifacts/test-gates/source-control-load-recovery-definition.json`.
`scripts/source_control_container_smoke.py` und
`scripts/source_control_control_center_harness.py` erzeugen erst mit
`--execute` echte Evidence. Definition oder Plan enthalten keine erfundenen
Latenzen, Erfolgsraten oder produktiven IDs. Fuer den Load/Recovery-Gate gelten
mindestens `100` Requests, Cursor-P95 `<= 500 ms`, Cursor-P99 `<= 1000 ms`,
Fehlerrate `< 2,0 %` und eine identische, lueckenfreie Event-Replay-Sequenz.

Die Legacy-Adoption wird ausschliesslich im Hub ausgefuehrt:

```text
ananta hub source-control-migration dry-run --tenant-id ... --project-id ... --owner-id ...
ananta hub source-control-migration apply --tenant-id ... --project-id ... --owner-id ... --idempotency-key <migration_id>
ananta hub source-control-migration resume --tenant-id ... --project-id ... --owner-id ... --idempotency-key <migration_id>
ananta hub source-control-migration rollback --tenant-id ... --project-id ... --owner-id ... --migration-id ... --idempotency-key rollback:<migration_id>
```

Der Dry-Run liefert die deterministische `migration_id`. Apply und Resume
akzeptieren nur genau diese ID als Idempotency-Key. Der Entrypoint gibt nur
Scope-IDs, Zaehler, Digests und Reason Codes aus, nie Legacy-Inhalt, Pfade,
URLs oder Secrets.

Produktionsverifikation bleibt `unverified` und `release_allowed=false`, solange
keine tatsaechlich bereitgestellten gueltigen Source- und Run-IDs samt Evidence
vorliegen. IDs duerfen niemals fuer Dokumentation, Fixture oder Gate erfunden
werden.
