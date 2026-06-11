# Security: ananta-worker Workspace Mutation Policy

AWWPI-004. Sicherheitsregeln für alle mutierenden ananta-worker-Läufe.
Implementierung: `agent/services/ananta_workspace_mutation_policy.py`,
durchgesetzt im Mutations-Loop (`agent/common/sgpt_workspace_mutation.py`)
und im Artifact-Sync (`WorkerWorkspaceService._mutation_sync_filter`).

## Scope-Regeln

1. **Nur explizit materialisierte oder policy-erlaubte Dateien dürfen
   geändert werden.** Materialization-Manifest-Einträge
   (`.ananta/materialization-manifest.json`) mit `allowed_operations`
   `write`/`patch` sind der primäre Scope; zusätzlich greifen
   `allowed_new_file_globs` (z. B. `tests/test_*.py`).
2. Änderungen außerhalb dieses Scopes → `policy_violation`,
   Pfad landet in `blocked_changes`.
3. **Mutationen außerhalb der WorkspaceRoot sind blockiert** (Resolver
   prüft Pfad-Containment).

## Immer blockiert

- **Path Traversal** (`..`) und **absolute Hostpfade**
- **`.git`**, **`.ananta`**, **`rag_helper`**, `__pycache__`
- **Secret-artige Dateien**: `.env*`, `*.pem`, `*.key`, `id_rsa*`,
  `*secret*`, `*credential*`, `.npmrc`, `.netrc`
- **Löschen/Umbenennen/große Datei-Ersetzungen**: gelöschte Dateien werden
  als `delete_or_rename_requires_separate_approval` blockiert; große Writes
  begrenzt `max_write_file_bytes`; Binary-Ersetzung ist abgelehnt.

## Eskalation controlled_workspace → strict_patch_request

Automatisch, wenn

- die Task-Risk-Einstufung in `escalate_to_strict_risks` liegt
  (Default: `high`, `critical`), **oder**
- ein geänderter Pfad einen `strict_path_markers`-Treffer hat
  (auth, oidc, keycloak, deployment, kubernetes, secret, security,
  .github, docker-compose) — dann setzt der PolicyResult
  `escalate_to_strict: true` und der Lauf gilt nicht als regulärer Erfolg.

## Baseline- und Artefakt-Pflichten (AWWPI-DD-003 / ALWA-013)

- Vor jeder mutierenden Ausführung wird eine **Baseline** erstellt
  (`refresh_mutation_baseline`); `read_only` braucht keine.
- Fehlende Baseline wird nicht stillschweigend übergangen: der Refresh
  liefert eine Warning in den Report.
- Jede Mutation erzeugt **DiffResult + PolicyResult**; meaningful Diffs
  ignorieren `.ananta`/`rag_helper`.
- **Bei `policy_blocked` werden keine Erfolgsartefakte registriert**: der
  Sync-Pfad liest `.ananta/mutation-report.json` und unterdrückt
  workspace_file-/workspace_diff-Artefakte vollständig; einzelne geblockte
  Pfade werden gefiltert.
- **ALWA-013 Pflicht-Audit**: das Erzeugen oder Aktualisieren der Baseline
  emittiert `workspace_baseline_created` via
  `audit_workspace_mutation_event` mit
  `task_id` / `goal_id` / `trace_id`, `mutation_mode`,
  `baseline_id` (Pfad), `baseline_hash` (sha256 des
  `manifest.json` der Snapshot-Kopie),
  `workspace_root_hash_or_id`, `materialized_paths_count`.
  `read_only` emittiert **kein** `workspace_baseline_created` (per Spec).

## Audit

`workspace_mutation_evaluated` (pro Iteration) und
`workspace_mutation_blocked` (Policy-Verletzungen + final-answer-Block)
über `audit_workspace_mutation_event` mit
`task_id` / `goal_id` / `trace_id` / `iteration_number`,
`mutation_mode`, `changed_paths` (sortiert, max 50 mit Count + Truncated-Flag),
`diff_hash` (sha256 über den gekürzten Diff-Text pro Iteration,
`diff_artifact_id` im finalen Sync-Event),
`policy_decision` (allowed / violation / unknown),
`violation_ids` / `violation_summary` (kurze Pfad:Reason-Summaries),
`blocked_reason` bei Block-Events.

Die früheren Event-Namen `ananta_worker_mutation_evaluated` /
`ananta_worker_mutation_blocked` sind deprecated Aliase auf die kanonischen
`workspace_*`-Werte — keine zwei Event-Reihen für dasselbe Ereignis.

**ALWA-DD-006 Redaction**: kein `prompt` / `raw_messages` / `raw_response` /
`file_content` / `unified_diff` / `full_diff` / `before` / `after` im
Audit. Der Helper droppt diese Felder explizit, auch wenn sie via
`**extras` reinkommen. `changed_paths` werden sortiert + auf 50 begrenzt
mit `changed_paths_count` + `changed_paths_truncated=true`.
