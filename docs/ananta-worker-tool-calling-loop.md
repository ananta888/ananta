# ananta-worker Tool Calling Loop

Track: `todos/todo.ananta-worker-tool-calling-loop.json` (AWTCL).
Vertrag: `docs/contracts/ananta-worker-tool-loop.md`.
Security: `docs/security/ananta-worker-tool-calling-policy.md`.

## Ist-Zustand vor dem Track (AWTCL-001)

Der ananta-worker ist in `agent/common/sgpt.py` als CLI-Backend
registriert und nutzt technisch `python -m sgpt`. Sein bisheriger Loop
(`_run_ananta_worker_iterative`):

1. lädt CodeCompass-Kontext in **Batches**
   (`agent/common/sgpt_architecture_scan.py::_load_source_file_batches`),
2. verarbeitet Batch für Batch und schreibt `rag_helper/progress.md`,
3. erzeugt am Ende eine **finale Synthese**.

Für `architecture_full_scan` existiert ein eigener
Plan-/Batch-/Summary-Loop (`_run_architecture_full_scan`). Was fehlte:
eine echte **ToolRequest/ToolResult-Schleife** — LLM → ToolRequest →
Hub-Policy → deterministische Ausführung → ToolResult als Evidence in die
nächste LLM-Runde. Genau diese Schleife liefert der Track.

## Architektur

```
ananta-worker LLM ──(JSON: tool_request)──► Hub
                                            │ 1. Tool Registry (bekannt? Schema?)
                                            │ 2. Policy Gate (allow/blocked/approval)
                                            │ 3. Audit (request)
                                            │ 4. deterministische Ausführung
                                            │ 5. Audit (result)
LLM ◄──(ToolResult als Evidence)────────────┘
```

Bausteine:

| Baustein | Datei |
|---|---|
| Loop + Parser + Prompt-Vertrag | `agent/common/sgpt_tool_loop.py` |
| Tool Registry | `agent/services/ananta_tool_registry_service.py` |
| Policy Gate | `agent/services/ananta_tool_policy_service.py` |
| Tool-Executors | `agent/services/tools/` |
| Evidence-Modell | `agent/services/tools/_evidence.py` |
| Audit-Events | `agent/common/audit.py` (`AUDIT_WORKER_TOOL_*`) |
| Diagnostics-API | `agent/routes/worker_tool_loop_diagnostics.py` |
| UI | `frontend-angular/.../worker-loop-diagnostics.component.ts` |

## Tool-Registry (Initialbestand)

- **read_only**: `repo.list_files`, `repo.read_file_range`, `repo.grep`,
  `codecompass.search`, `codecompass.expand_graph`,
  `codecompass.architecture_query`, `git.status`, `git.diff_readonly`,
  `workspace.diff`
- **controlled_execution**: `test.discover`, `test.run`,
  `shell.run_allowlisted` (approval), `opencode.propose`, `hermes.review`,
  `aider.propose`, `codex.propose`
- **controlled_write**: `repo.write_file`, `repo.apply_patch`,
  `todo.create_or_update` (approval), `git.add_selected` (approval)
- **blocked**: `shell.run_unrestricted`, `network.fetch_arbitrary`,
  `service.restart`, `secret.read`, `git.push`, `git.commit`,
  `external_worker.execute_mutation`

## Prompt-Instruktionen (AWTCL-012)

`build_tool_loop_instructions()` erklärt dem Modell die erlaubten
Output-Kinds und das ToolRequest-Schema und enthält die zwei harten
Regeln:

- **Nicht raten** — fehlen deterministische Daten, Tool anfordern oder
  `cannot_continue_without_context` melden.
- **Keine Ausführung behaupten**, die der Hub nicht per ToolResult
  bestätigt hat.

## Konfiguration (AWTCL-004)

```jsonc
"ananta_worker_tool_loop": {
  "enabled": false,            // Default aus — Batch-Loop bleibt Fallback
  "max_iterations": 6,
  "max_tool_calls": 12,
  "max_tool_result_chars": 8000,
  "max_invalid_outputs": 2,
  "allowed_tools": ["repo.list_files", "repo.read_file_range", "repo.grep",
                     "codecompass.search", "codecompass.expand_graph",
                     "codecompass.architecture_query", "git.status", "git.diff_readonly"]
}
```

## Rollout-Plan (AWTCL-020)

1. **Stufe 0 (heute):** Flag aus; Verhalten unverändert
   (Batch-Loop/`architecture_full_scan`, Tests grün).
2. **Stufe 1:** Flag an mit ausschließlich read-only `allowed_tools`
   (Default-Liste oben). Kein Approval-Bedarf, kein Mutationsrisiko.
3. **Stufe 2:** `test.discover`/`test.run` mit gepflegter
   `allowlisted_test_commands`-Liste.
4. **Stufe 3:** externe Propose-/Review-Tools (`opencode.propose`,
   `hermes.review`, …) — weiterhin mutationsfrei.
5. **Stufe 4:** write/execution Tools nur zusammen mit dem
   Workspace-Mutations-Track (`docs/ananta-worker-workspace-patch-iteration.md`)
   und dessen Rollout-Stufen.

Fallback jederzeit: Flag aus → alter Batch-Loop. Tests:
`tests/test_ananta_worker_tool_loop.py`,
`tests/test_ananta_worker_tool_policy.py`.
