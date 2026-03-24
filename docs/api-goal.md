# DOC-GOAL-802: Goal and Plan API reference (short)

Endpoints
---------
POST /goals
- Beschreibung: Erzeuge ein neues Goal; das System erstellt optional einen Plan und Tasks.
- Request Body (Beispiel):
  {
    "goal": "Implement login feature",
    "context": "Optionaler Text oder Repo-Kontext",
    "create_tasks": true,
    "use_repo_context": false
  }
- Erfolgsantwort (201):
  {
    "data": {
      "goal": {"id": "goal-...", "goal": "Implement login feature", "task_count": 3},
      "created_task_ids": ["task-...", ...],
      "subtasks": [{"title": "...","description":"..."}, ...],
      "workflow": {"defaults": {...}, "overrides": {...}, "effective": {...}},
      "readiness": {...}
    }
  }

GET /goals/{goal_id}
- Liefert Goal und task_count

GET /goals/{goal_id}/plan
- Liefert persistierten Plan und PlanNodes, falls aktiviert

Notes
-----
- Feature Flags: `goal_workflow_enabled` und `persisted_plans_enabled` steuern Verhalten.
- Backwards compatibility: Alte Task-APIs bleiben unverändert; Goal-API ist additive.

Referenzen
----------
- docs/architecture/goal-model.md
- docs/migration-legacy-clients.md
