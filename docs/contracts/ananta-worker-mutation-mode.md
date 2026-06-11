# Contract: ananta-worker mutation_mode

AWWPI-002. Definiert die vier Mutationsmodi des ananta-worker und ihre
Auflösung. Implementierung:
`agent/services/ananta_workspace_mutation_policy.py::resolve_mutation_mode`,
Konfiguration: `ananta_worker_workspace_mutation` in
`agent/config_defaults.py`.

## Modi

### `read_only`

- Analyse-/Review-Modus, **keine** Datei-Mutationen.
- Erlaubte Tools: `repo.list_files`, `repo.read_file_range`, `repo.grep`,
  `codecompass.search`, `codecompass.expand_graph`, `git.diff_readonly`.
- Default-Einsatz: architecture_review, repo_analysis, security_review,
  planning. Keine mutierende Baseline nötig.

### `controlled_workspace` (expliziter Kompatibilitätsmodus)

- Hub setzt den Rahmen: Workspace-Grenzen, materialisierte Dateien
  (Manifest mit `allowed_operations`), erlaubte Tools.
- Worker darf **innerhalb dieses Rahmens selbst Dateien ändern**
  (`workspace_write`-Aktionen, von der Runtime direkt angewendet);
  der Hub wendet *nicht* jeden Patch einzeln an.
- Pflicht-Hub-Checks danach: workspace_root_boundary,
  materialized_file_scope, forbidden_path_filter, diff_against_baseline,
  artifact_sync, optional allowlisted Tests.
- Einsatz: kleine, explizit materialisierte Workspaces oder
  Dokumentations-/Generatorfälle, bei denen Full-File-Inhalt fachlich
  kontrollierbar ist.

### `strict_patch_request` (Default für Coding/Bugfix/Refactor)

- Worker darf **keine** Dateien direkt ändern; er liefert
  PatchRequest/WriteRequest (`docs/contracts/ananta-worker-workspace-patch.md`),
  der Hub validiert und wendet **einzeln** an.
- Default-Einsatz: coding, bugfix, refactor und test_update.
- Erforderlich für: security_policy_files, auth_oidc_keycloak,
  deployment_kubernetes, secrets_or_config, large_refactor,
  destructive_change, outside_initial_scope.
- Brownfield-Ablauf: `codecompass.plan_context` oder `repo.grep` →
  `repo.read_file_range` → `patch_request` → `workspace.diff` →
  allowlisted `test.run`.

### `external_agent_workspace`

- OpenCode/Aider/Codex-Modus: der externe Agent arbeitet im Workspace;
  Ananta kontrolliert Kontext, Workspace, Diffs, Artefakte und Approvals
  außen herum (bestehender OpenCode-Pfad, unverändert).

## Auflösung

```
explicit (research-context.mutation_mode oder Config mutation_mode)
  > mode_by_task_kind-Mapping
    > read_only (Fallback)
```

- **Unbekannter mutation_mode** → Fallback `read_only` (nie Ausführung mit
  undefiniertem Modus).
- **Risk-Eskalation:** `controlled_workspace` + risk ∈
  `escalate_to_strict_risks` (Default: high, critical) →
  `strict_patch_request`.
- **Pfad-Eskalation:** Treffer der `strict_path_markers` (auth, oidc,
  keycloak, deployment, kubernetes, secret, security, .github,
  docker-compose) setzen `escalate_to_strict` im PolicyResult.

## Approval-Verhalten pro Modus

| Modus | Direkte Writes | repo.write_file | repo.apply_patch | test.run |
|---|---|---|---|---|
| read_only | – | blocked | blocked | allowlisted |
| controlled_workspace | ja (im Manifest-Scope) | allow | blocked | allowlisted |
| strict_patch_request | nein | allow (hub-applied) | allow (hub-applied) | allowlisted |
| external_agent_workspace | externer Agent | n/a | n/a | n/a |

`todo.create_or_update` und `git.add_selected` benötigen in jedem Modus ein
separates Hub-Approval.
