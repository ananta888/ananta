# Planning Blueprint Flow

## Ziel

Dieses Dokument beschreibt die aktuelle Aufloesungsreihenfolge fuer Goal-Planning und die Trennung der Verantwortlichkeiten zwischen Planning Templates und Team Blueprints.

## Aufloesungsreihenfolge im Planner

1. `PlanningTemplateCatalog` (exakte Template-ID, dann Keyword-Match)
2. Blueprint-backed Planning Adapter (Subtasks aus Blueprint-Artefakten + Provenance-Hinweise)
3. HubCopilot Planning Strategy
4. LLM Planning Strategy (Fallback)

Der execution-focused Fallback ist ausgelagert und wird nur nachrangig genutzt.

## Verantwortlichkeiten

### PlanningTemplateCatalog

- Quelle fuer AutoPlanner-Subtask-Templates.
- Datenbasiert ueber `config/planning_templates.json`.
- Validiert ueber Schema `schemas/planning/planning_template_catalog.v1.json`.

### Seed Blueprint Catalog

- Quelle fuer Standard-Seed-Blueprints.
- Datenbasiert ueber `config/blueprints/standard/blueprints.json`.
- Validiert ueber Schema `schemas/blueprints/seed_blueprint_catalog.v1.json`.
- Wird fuer Seed-Reconcile in `routes/teams.py` ueber `SeedBlueprintCatalog` geladen.

### planning_utils (Legacy/Utilities)

- Bleibt fuer technische Hilfsfunktionen:
  - Input Sanitizing und Validation
  - JSON-Extraktion und Parsing
  - Subtask-Normalisierung und Follow-up-Parsing
- Legacy `match_goal_template` ist kompatibel, aber als deprecated markiert.
- Keine fachliche Hauptquelle fuer umfangreiche Hardcoded Templates.

## Erweiterung neuer Domain-Blueprints

Neue Domain-Blueprints sollen additiv eingebunden werden:

1. Seed-Daten im Blueprint-Katalog erweitern.
2. Optional korrespondierende Planner-Templates im PlanningTemplateCatalog ergaenzen.
3. Reconcile/Regression-Tests ergaenzen.
4. Keine grossen neuen Seed-/Task-Dictionaries in Route-Modulen einfuehren.
