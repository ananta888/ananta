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

## Aktivierungsstand der neuen Onboarding-Pfade

- Credential-freie Public Remotes sind lokal nur per explizitem Compose-Opt-in
  aktiv. Produktion verwendet standardmaessig
  `ANANTA_SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED=false`.
- Workspace-Folder-Registration bleibt auf den projektgebundenen
  `ANANTA_WORKSPACE_ROOT`, opaque Handles, read-only Aufloesung und
  content-freies Audit begrenzt.
- Private GitHub-App-/OAuth-Nutzung bleibt fail-closed, solange Provisioner,
  Secret Resolver, reale Installation und Grant nicht extern konfiguriert sind.
- Die aktuellen Backend-Gates liefen mit `167/167`, der
  PostgreSQL-Identifierfix mit `14/14` und der Auth-Produktionsfix mit
  `147/147`. Angular lief mit `68/68`, erfolgreichem Build und `42/42`
  Project-Selector-Tests; die Planning-Gates waren gruen.
- Ein Live-PostgreSQL-Upgrade erreichte den Head-Praefix `5c0f...`; Hub und
  Angular waren healthy. Workspace List `200`, Validate `200`, Create `201`,
  Catalog-Match und Disable `200` verifizieren die lokale
  Workspace-Registration auf Backend-/Integrationsniveau.
- Public Git/GitHub ist lokal durch Chromium `1/1` ohne Retry verifiziert:
  Validate und Create fuer `octocat/Hello-World@master`, immutable
  Commit-Pinning, Connection, Refresh und Scan-ready bestanden. Der erwartete
  Health-Fehler des nicht konfigurierten privaten Providers erzeugte keine
  irrefuehrende globale Fehlermeldung.
- Die Workspace-Snapshot-Chromium-Journey bestand `1/1` ohne Retry in `7,3`
  Minuten. Sie umfasste Upload, Connection, Refresh, Scan, dynamische Policy
  und Grant, Index-Run, realen Claim mit `900` Sekunden Lease,
  Propose/Execute, capability-gebundene Ausgabematerialisierung, atomare
  idempotente Hub-Projektion, explizite Aktivierung mit Active-Pointer-CAS
  `active:0` sowie UI-Readback.
- Worker-Ausgaben verwenden eine signierte, kurzlebige und an Job, Assignment,
  Lease sowie Artefakt gebundene Capability. Nutzer- oder Hub-Bearer werden
  nicht an Worker weitergegeben. Die fokussierten Output-Tests bestanden
  `18/18`, die Completion-/Projection-Suite `22/22`.
- Graph und Query antworteten nach dem Authorization-Whitelist-Regressionsfix
  live jeweils mit HTTP `200`; der fokussierte Regressionstest bestand `1/1`.
- Die lokale Compose-E2E-Konfiguration betreibt den Autopilot bewusst nicht.
  Der Test-Harness treibt Claim, Propose und Execute ueber den Hub mit einer
  gebundenen Lease; daraus entsteht keine Worker-zu-Worker-Orchestrierung.
- Historische Gate-Evidence bleibt erhalten, belegt aber nicht automatisch
  den aktuellen Implementierungsdelta. Neue `SRC_*`- oder `RUN_*`-IDs werden
  ohne reale Bereitstellung nicht eingetragen.

Der stabile lokale Nachweis liegt in
`artifacts/test-gates/source-control-center-live-browser-verification.json`.
Er enthaelt keine Lauf-IDs oder Zeitstempel und ist ausdruecklich kein
Produktionsnachweis. Private GitHub-App-/OAuth-, Claude-/Cloud-, Receiver-,
Paging- und Produktionsmetriken-Journeys bleiben extern offen.
