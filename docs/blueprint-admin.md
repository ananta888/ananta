# Blueprint- und Rollen-Template-Admin

Diese Notiz beschreibt den aktuellen Admin-Ist-Zustand fuer Blueprints, Rollen-Templates (API: `templates`) und blueprint-basierte Team-Erstellung.

## Zielbild

- **Blueprint-first** bleibt der bevorzugte Pfad fuer Team-Aufbau.
- Der Hub bleibt Orchestrator; Blueprints definieren nur wiederverwendbare Struktur, keine eigene Worker-Orchestrierung.
- Seed-Blueprints werden beim Lesen deterministisch mit den Code-Definitionen abgeglichen.
- Public Model (Standard Mode): **Role Template -> Blueprint -> Team**.

## Blueprints

### Produktkatalog (Standard-Modus)

- `GET /teams/blueprints/catalog` liefert die vereinfachte Produktsicht fuer den Standardmodus.
- Pro Blueprint sind u. a. `intended_use`, `when_to_use`, `expected_outputs`, `safety_review_stance` und `work_profile_summary` enthalten.
- `work_profile_summary` enthaelt empfohlene Goal-Modi, Playbook-Hinweise, Capability-Hinweise und ein lesbares Governance-Profil.

### Seed-Reconcile

- `GET /teams/blueprints` und `GET /teams/blueprints/<id>` triggern vor der Antwort einen Seed-Abgleich.
- Bestehende Seed-Blueprints werden **nicht** stillschweigend ignoriert, sondern diff-basiert reconciled.
- Unveraenderte Rollen/Artefakte behalten dabei ihre IDs.
- Audit-Events `team_blueprint_reconciled` enthalten differenzierte Change-Sets fuer Rollen und Artefakte.

### Validierungsregeln

Beim Erstellen/Aktualisieren eines Blueprints gelten u. a. folgende Regeln:

- Blueprint-Name muss gesetzt sein.
- Rollen-Namen innerhalb eines Blueprints muessen eindeutig sein.
- `sort_order` fuer Rollen muss innerhalb eines Blueprints eindeutig sein.
- Artefakt-Titel innerhalb eines Blueprints muessen eindeutig sein.
- `sort_order` fuer Artefakte muss innerhalb eines Blueprints eindeutig sein.
- Aktuell erlaubter Artefakt-Typ fuer Materialisierung ist `task`.
- Referenzierte `template_id`-Werte muessen existieren.

### Delete-Semantik

- `DELETE /teams/blueprints/<id>` loescht keinen Blueprint mehr, wenn Teams darauf verweisen.
- Die API liefert dann `409 blueprint_in_use` inklusive `team_ids` und `team_count`.
- Das Frontend zeigt dafuer eine explizite Admin-Fehlermeldung.

### Auditierbarkeit

- `team_blueprint_created`
- `team_blueprint_updated`
- `team_blueprint_reconciled`
- `team_blueprint_deleted`
- `team_blueprint_instantiated`

Fuer Create/Update/Reconcile enthalten die Audit-Details jetzt:

- `blueprint_fields`
- `roles.created|updated|deleted`
- `artifacts.created|updated|deleted`

Damit lassen sich Seed-Drift, Admin-Aenderungen und Child-Diffs direkt im Audit nachvollziehen.

## Rollen-Templates (API: Templates)

### Namensregeln

- Rollen-Template-Namen werden getrimmt gespeichert.
- Mehrdeutige Namen sind ausgeschlossen.
- API und DB erzwingen die Eindeutigkeit ueber `uq_templates_name`.
- Konflikte antworten mit `409 template_name_exists`.

### Template-Variablen

- Standard: unbekannte `{{variablen}}` erzeugen Warnungen, blockieren den Save aber nicht.
- Optional strict: `template_variable_validation.strict=true` in `/config` oder `config.json`
- Optional kann ein fester Kontext erzwungen werden: `template_variable_validation.context_scope`.
- Im Strict-Mode antwortet die API differenziert mit:
  - `400 unknown_template_variables`
  - `400 context_unavailable_template_variables`
  - `400 template_validation_failed`
- Validierung/Vorschau:
  - `POST /templates/validate`
  - `POST /templates/preview`
  - `POST /templates/validation-diagnostics`

## Team-Instanziierung aus Blueprint

- `POST /teams/blueprints/<id>/instantiate` speichert einen Snapshot der verwendeten Blueprint-Definition am Team.
- Start-Artefakte vom Typ `task` werden als Team-Tasks materialisiert.
- Blueprint-Rollen werden fuer die Team-Struktur auf bestehende Rollen gemappt oder kontrolliert erzeugt.
- Seed-Reconcile und Instanziierung sind gemeinsam durch Backend-Integrationstests abgesichert.
- Team-Listen liefern additiv `user_lifecycle_state` mit vereinfachten Statuslabels (`Standard`, `Angepasst`, `Aktualisierbar`) fuer den normalen Nutzerfluss.

## Blueprint als direktes Arbeitsprofil

- `GET /teams/blueprints/<id>/work-profile` liefert ein direkt verwendbares Profil fuer den Blueprint.
- Das Profil kombiniert:
  - empfohlene Goal-Modi (z. B. `code_fix`, `docker_compose_repair`)
  - Playbook-Empfehlungen
  - role-basierte Capability-Hinweise aus den Blueprint-Rollen
  - enthaltene Policy-Artefakte als Default-Governance-Profil
- Damit sind Blueprints nicht nur Verwaltungsobjekte, sondern ein greifbarer Startpunkt fuer konkrete Arbeitsmodi.

## Passende Referenzen

- Public model: `docs/blueprint-product-model.md`
- Standard blueprint catalog: `docs/standard-blueprints.md`
- API: `api-spec.md`
- Bundle-Import/Export: `docs/blueprint-bundle-import-export.md`
- Testbetrieb: `docs/testing.md`
- Template Authoring: `docs/template-authoring-guide.md`
- Template Registry/Contract: `docs/template-variable-registry.md`
- Template Migration: `docs/template-variable-migration-notes.md`
