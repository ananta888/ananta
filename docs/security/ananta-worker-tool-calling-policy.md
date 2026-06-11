# Security: ananta-worker Tool Calling Policy

AWTCL-003/006. Sicherheitsgrenzen für den Tool-Calling-Loop des
ananta-worker. Implementierung:
`agent/services/ananta_tool_policy_service.py` (Gate) und
`agent/services/ananta_tool_registry_service.py` (Registry).

## Grundsätze

1. **Der Hub bleibt finaler Entscheider.** Das Worker-LLM fordert Tools nur
   an; Ausführung, Approval und Audit liegen zentral beim Hub.
2. **Unbekannte Tools werden deterministisch abgelehnt** (`unknown_tool_rejected`).
3. **Approvals werden nie automatisch erteilt.** `approval_required` ist ein
   Endzustand des Gates, kein Durchlauf.

## Risk-Klassen

| Klasse | Bedeutung | Beispiele |
|---|---|---|
| `read` | rein lesend, reproduzierbar | repo.grep, codecompass.search, git.status |
| `execution` | kontrollierte Ausführung mit Limits | test.run (allowlisted), shell.run_allowlisted |
| `write` | Workspace-Mutation | repo.write_file, repo.apply_patch |
| `admin` | System-/Repo-Verwaltung | service.restart, git.push |
| `external_agent` | externe Backends | opencode.propose, hermes.review |

## Hart geblockt ohne separates Approval

Diese Tools laufen **nie** über den Worker-Loop, auch nicht mit
Approval-Token (Kategorie `blocked`):

- `shell.run_unrestricted`
- `network.fetch_arbitrary`
- `service.restart`
- `secret.read`
- `git.push`, `git.commit`
- `external_worker.execute_mutation`

## Gate-Regeln

- **Read-only** Tools laufen ohne Approval, wenn sie in `allowed_tools`
  (Scope) enthalten sind.
- **Write/Execution** Tools können `policy_blocked` (falscher
  mutation_mode, außerhalb Scope) oder `approval_required`
  (`requires_approval` ohne erteiltes Approval) liefern.
- **Hermes**: Nur `hermes.review` ist zugelassen (planning/review/
  summarize/patch_propose/research_limited gemäß ToolRoutingService);
  alle anderen `hermes.*`-Capabilities — insbesondere shell_execution und
  patch_apply — werden geblockt und nicht durch den ananta-worker umgangen.
- **OpenCode/Aider/Codex**: nur als `*.propose` (Vorschlag/Review).
  Mutationen externer Agenten laufen ausschließlich über die regulären
  Hub-Approval-Pfade; der Propose-Prompt verbietet Datei-/Shell-Aktionen
  explizit. Backend-Auswahl über `ToolRoutingService` (AWTCL-016).
- **Mutation-Mode-Gate**: `repo.write_file` nur in `controlled_workspace`
  oder `strict_patch_request`; `repo.apply_patch` nur in
  `strict_patch_request`. In `read_only` ist jede Mutation geblockt.

## Pfad- und Output-Grenzen

- Repo-Tools arbeiten nur innerhalb der Workspace-Wurzel; absolute Pfade
  und Path Traversal werden abgewehrt
  (`agent/services/tools/repo_tools.py::resolve_workspace_path`).
- Outputs sind begrenzt, sortiert und deterministisch; Kürzungen werden als
  `evidence_truncated` ausgewiesen.
- Audit: jeder Request/Result mit task_id/session_id, tool_name,
  policy_decision und risk_class; Prompts/Roh-Outputs gelangen nicht ins
  Audit (Redaction + `_FORBIDDEN_RAW_FIELDS` + Excerpt-Limit).
