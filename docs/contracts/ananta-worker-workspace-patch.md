# Contract: PatchRequest / PatchResult (strict_patch_request)

AWWPI-003. Definiert die Patch-Verträge für den
`strict_patch_request`-Modus. Implementierung:
`agent/common/sgpt_workspace_mutation.py` (Loop) und
`agent/services/tools/workspace_mutation_tools.py` (Anwendung).

> **Wichtig:** `strict_patch_request` ist der bevorzugte Default für
> Coding-, Bugfix- und Refactor-Aufgaben. `controlled_workspace` bleibt
> ein expliziter Kompatibilitätsmodus für kleine, eng erlaubte Workspaces
> (siehe `docs/contracts/ananta-worker-mutation-mode.md`).

## PatchRequest (LLM → Hub)

```json
{
  "kind": "patch_request",
  "target_path": "agent/services/example.py",
  "variant": "unified_diff",
  "unified_diff": "--- a/agent/services/example.py\n+++ b/...\n@@ -10,3 +10,3 @@\n-old\n+new\n context",
  "expected_old_hash": "sha256-des-aktuellen-dateiinhalts",
  "reason": "Fix off-by-one in pagination",
  "risk_hint": "write"
}
```

Varianten (`variant`):

| Variante | Bedeutung | Pflichtfelder |
|---|---|---|
| `unified_diff` | Hunk-basierter Patch auf bestehende Datei | `unified_diff` |
| `replace_range` | Ersetzt einen kleinen LineRange in bestehender Datei | `line_start`, `line_end`, `replacement` |
| `write_file_create_only` | Neue Datei anlegen | `content` |

- `expected_old_hash` ist für bestehende Dateien empfohlen und wird, wenn
  vorhanden, vor jeder Anwendung geprüft. Bei Konflikt wird abgelehnt statt
  halb angewendet.
- `target_path` ist workspace-relativ; absolute Pfade und Traversal werden
  abgelehnt.
- `replace_range` ist auf kleine Bereiche begrenzt
  (`max_replace_range_lines`, Default 120), damit der Worker keine
  Full-File-Rewrites als Range-Patch tarnt.
- `repo.write_file replace_existing` ist nur für kleine bestehende Dateien
  gedacht (`max_replace_existing_bytes`, Default 64 KiB); große Dateien
  müssen über `unified_diff` oder `replace_range` geändert werden.

## PatchResult (Hub → LLM)

Als `ananta_tool_result.v1` mit `data`:

```json
{
  "schema": "ananta_tool_result.v1",
  "tool_call_id": "patch_result:1",
  "tool_name": "repo.apply_patch",
  "status": "ok",
  "data": {
    "applied": true,
    "rejected_reason": null,
    "changed_files": ["agent/services/example.py"],
    "diff_excerpt": "…",
    "content_hashes": {"agent/services/example.py": "sha256…"},
    "reason": "Fix off-by-one in pagination"
  },
  "warnings": []
}
```

Ablehnungsgründe (`rejected_reason`), Auswahl:

- `expected_old_hash_mismatch` — Datei hat sich geändert; neuen Stand lesen.
- `hunk_context_mismatch` — Diff passt nicht auf den Inhalt; **keine**
  Teilanwendung.
- `path_traversal_blocked` / `absolute_path_blocked` / `target_file_not_found`
- `file_already_exists` (create_only), `binary_file_replace_blocked`,
  `content_too_large`, `replace_requires_expected_old_hash_or_approval`
- `max_patch_attempts_per_file_exceeded` — Loop-Abbruch nach konfigurierter
  Versuchszahl pro Datei.

## Feedback-Einbettung

Nach jeder Patch-Anwendung erzeugt der Hub zusätzlich DiffResult und
PolicyResult gegen die Baseline (`ananta_workspace_feedback.v1`) und bettet
beides zusammen mit dem PatchResult in die nächste LLM-Iteration ein.
