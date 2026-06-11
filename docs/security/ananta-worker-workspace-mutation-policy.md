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

## Baseline- und Artefakt-Pflichten (AWWPI-DD-003)

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

## Audit

`ananta_worker_mutation_evaluated` / `ananta_worker_mutation_blocked` mit
task_id/session_id, Policy-Status und Changed-Files-Excerpt — ohne
Roh-Prompts und ohne Dateiinhalte.
