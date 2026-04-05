# Rollout fuer Blueprint- und Template-Migrationen

Diese Anleitung beschreibt den sicheren Rollout der aktuellen Blueprint-/Template-Haertungen in produktionsnahen Umgebungen.

## Betroffene Migrationen

1. `c9d0e1f2a3b4_add_blueprint_child_uniqueness.py`
2. `d1e2f3a4b5c6_add_template_name_uniqueness.py`

## Inhalt des Rollouts

### `c9d0e1f2a3b4`

- bereinigt doppelte Blueprint-Rollen pro `(blueprint_id, name)`
- bereinigt doppelte Blueprint-Artefakte pro `(blueprint_id, title)`
- normalisiert kollidierende `sort_order`-Werte
- fuegt DB-Constraints fuer Blueprint-Children hinzu

### `d1e2f3a4b5c6`

- trimmt Template-Namen
- entfernt mehrdeutige Template-Dubletten
- fuegt `uq_templates_name` hinzu

## Empfohlene Reihenfolge

1. Anwendungscode mit neuer API-/Validierungslogik deployen.
2. Vor dem Schema-Upgrade Datenbank-Backup erstellen.
3. Kritische Bestandsdaten pruefen:
   - doppelte Template-Namen
   - doppelte Blueprint-Rollenamen
   - doppelte Blueprint-Artefakt-Titel
   - kollidierende `sort_order`-Werte
4. Alembic-Migrationen ausrollen.
5. Danach Smoke-Checks fahren:
   - `GET /teams/blueprints`
   - `POST /teams/blueprints` mit gueltigem Payload
   - `POST /templates` mit neuem Namen
   - `POST /teams/blueprints/<id>/instantiate`
6. Audit-Logs auf `team_blueprint_reconciled`, `team_blueprint_updated` und `template_*` pruefen.

## Backup-Checks

Vor dem Rollout mindestens sichern:

- komplette relationale Datenbank
- relevante `config.json`/Admin-Konfiguration
- existierende Export-Bundles fuer kritische Blueprints, falls vorhanden

## Rueckfallstrategie

### Wenn Migration fehlschlaegt

- Deployment stoppen
- Datenbank auf Backup zuruecksetzen
- Dubletten/Kollisionen ausserhalb der Migration explizit bereinigen
- Migration erneut gegen bereinigten Stand laufen lassen

### Wenn Anwendung nach Migration fachlich auffaellig ist

- zuerst nur Application-Rollback pruefen, wenn das Schema unveraendert kompatibel bleibt
- falls Datenbereinigung unerwuenschte Effekte hatte: DB-Restore aus Backup
- danach problematische Blueprints/Template-Namen mit Bundle-Export bzw. Audit-Diffs rekonstruieren

## Operative Hinweise

- Die Migration `c9d0e1f2a3b4` haengt in einer verzweigten Alembic-Historie an `b8c9d0e1f2a3`. Vor produktivem Rollout den gesamten Upgrade-Pfad der Zielumgebung gegen den realen Revisionsgraphen pruefen.
- Die Datenbereinigung ist bewusst deterministisch, aber nicht verlustfrei fuer echte Dubletten: doppelte Rows werden entfernt. Deshalb ist ein Backup vorab Pflicht.
- Nach dem Rollout greifen dieselben Regeln bereits auf API-Ebene; neue Dubletten sollten also nicht mehr nachwachsen.
