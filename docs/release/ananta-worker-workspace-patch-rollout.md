# Rollout: ananta-worker Workspace Mutation (read_only → controlled_workspace → strict_patch_request)

AWWPI-020. Stufenplan für die Aktivierung der Workspace-Mutation des
ananta-worker. Feature-Flags: `ananta_worker_workspace_mutation.enabled`
und `ananta_worker_tool_loop.enabled` (beide Default **aus**); Fallback in
jeder Stufe: Flags aus → bestehender Analyse-/Batch-Loop
(`_run_ananta_worker_iterative`).

## Stufen

### Stufe 0 — Ausgangszustand (heute)
- Beide Flags aus. Verhalten identisch zu vorher; Regressionstests grün
  (`tests/test_sgpt_route.py`, `tests/test_e2e_workspace_artifact_flow.py`).

### Stufe 1 — read_only + diff-only Dry-Run
- `ananta_worker_workspace_mutation.enabled: true`,
  `mutation_mode: read_only` (oder Mapping nur auf read_only).
- Zusätzlich `ananta_worker_tool_loop.enabled: true` mit read-only Tools
  (`workspace.diff` eingeschlossen) — der Worker *sieht* Diffs/Policy,
  ändert aber nichts.
- Erfolgskriterium: Tool-Loop-Reports in der Diagnostik-UI, keine
  Mutationen, Audit-Events vorhanden.

### Stufe 2 — strict_patch_request für Coding/Bugfix/Refactor
- `mode_by_task_kind`: coding/bugfix/refactor/test →
  `strict_patch_request`.
- Worker nutzt bevorzugt `codecompass.plan_context`/`repo.grep`,
  `repo.read_file_range`, `patch_request`, `workspace.diff` und
  allowlisted `test.run`.
- Voraussetzung: Materialization-Manifest pro Task
  (`materialize_allowed_workspace_files`) — ohne Manifest blockiert
  `require_materialized_scope: true` jede Änderung.
- Erfolgskriterium: DiffResult/PolicyResult pro Iteration,
  `mutation-report.json` mit Outcome `final_answer`, Artefakt-Sync nur für
  policy-konforme Änderungen.

### Stufe 3 — Feedback-Iteration ohne test.run
- `max_feedback_iterations` ≥ 2 produktiv nutzen; DiffResult/PolicyResult
  als Evidence; NoProgressDetection beobachten (Outcome
  `no_progress_detected` in der Diagnostik).

### Stufe 4 — test.run allowlisted
- `allowlisted_test_commands` projektspezifisch pflegen (z. B.
  `python -m pytest tests/ -q`); Timeout/Output-Limits gelten immer.
- Erfolgskriterium: fehlgeschlagener Test führt nachweisbar zu einer
  zweiten gezielten Iteration.

### Stufe 5 — controlled_workspace nur explizit
- `escalate_to_strict_risks` und `strict_path_markers` aktiv lassen;
  security/auth/infra bleiben strict.
- Jeder Patch läuft über `repo.apply_patch`/`repo.write_file` mit
  Hash-Prüfung; Konflikte enden als `rejected_reason`, nie als halbe Datei.
- `controlled_workspace` nur noch für kleine, eng materialisierte
  Kompatibilitätsfälle aktivieren.

### Spätere, separate Stufen (nicht Teil dieses Rollouts)
- **write/delete/rename** außerhalb des Manifests sowie
  `todo.create_or_update`/`git.add_selected` bleiben approval-pflichtig;
  Lösch-/Umbenennungs-Operationen sind bis zu einer eigenen Approval-Stufe
  generell blockiert.
- `git.commit`/`git.push` bleiben dauerhaft außerhalb des Worker-Loops.

## Abbruchkriterien / Rollback

- Häufung von `policy_blocked`- oder `no_progress_detected`-Outcomes →
  Stufe zurück (Flag-Änderung genügt, kein Deploy nötig).
- Audit-Lücken oder unklare Diffs → Stufe 1 (diff-only) bis geklärt.

## Tests pro Stufe

- Stufe 1: `tests/test_ananta_worker_tool_loop.py`,
  `tests/test_ananta_worker_tool_policy.py`
- Stufe 2/3: `tests/test_ananta_worker_workspace_patch.py`,
  `tests/test_ananta_worker_workspace_feedback_iteration.py`
- Stufe 4: `test_failing_test_triggers_second_targeted_iteration`,
  `test_non_allowlisted_test_command_is_policy_blocked`
- Stufe 5: `test_strict_patch_request_applies_patch_via_hub`,
  `test_apply_patch_rejects_hash_conflict_without_partial_change`
