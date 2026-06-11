  # Contract: ToolProposal-Artefakt (`tool_proposal.v1`)

Track: `todos/todo.hub-direct-execution-dynamic-tools.json`
(HDE-010/HDE-014). Implementierung:
`agent/services/custom_tool_proposal_service.py`.

## Grundsatz

LLMs und User dürfen Tools **vorschlagen**, niemals registrieren oder
aktivieren (HDE-DD-003). Ein Proposal ist ein inertes, digest-gebundenes
JSON-Artefakt. Aktiv wird ein Tool nur über die Promotion-Pipeline
(`pending -> validated -> approval_required -> approved -> active`,
siehe `docs/security/custom-tool-promotion.md`).

## Pflichtfelder

| Feld | Typ | Regeln |
| --- | --- | --- |
| `name` | string | Namensraum `custom.*` oder `project.*` (`^(custom|project)\.[a-z][a-z0-9_]*…$`); darf keinen statischen Registry-Namen überschreiben |
| `description` | string | nicht leer |
| `proposed_by` | string | z. B. `user:<id>` oder `llm:<worker>` |
| `source_task_id` | string | Herkunfts-Task |
| `risk_class` | enum | `read` \| `execution` \| `write` |
| `category` | enum | `read_only` \| `controlled_execution` \| `controlled_write` |
| `execution_plane` | enum | `worker_runtime` \| `sandbox_runtime` (HDW-003; ohne gültigen Wert nicht aktivierbar) |
| `mutation_declaration` | enum | `read_only` \| `controlled_execution` \| `controlled_write` (HDE-018) |
| `argument_schema` | object | JSON-Schema-artig mit `properties` |
| `execution_kind` | enum | `command_template` \| `script` |
| `command_template` | string[] | nur bei `command_template`; Token-Liste, Platzhalter `{arg}` müssen in `argument_schema` existieren; statische Token ohne Shell-Metazeichen |
| `script_body_ref` | string | nur bei `script`; muss in `tool-scripts/` liegen (genehmigter Speicherort), kein `..` |
| `allowed_paths` / `denied_paths` | string[] | Globs relativ zum Workspace |
| `timeout_seconds` | int | 1–600 |
| `output_max_chars` | int | 1–100000 |
| `tests` | object[] | mindestens 1× `kind=positive` und 1× `kind=negative` |
| `approval_status` | enum | startet immer `pending`; Service überschreibt Fremdwerte |

**Keine freien Shell-Kommandos:** ein String statt einer Token-Liste
ist ungültig; Pipes, Substitutionen und Verkettungen in statischen
Tokens werden vom Schema abgelehnt, und das final gerenderte Kommando
durchläuft zusätzlich den `ShellCommandAnalyzer` (HDE-016).

## Optionale Felder (HDE-014: Intent-Aliase & Reuse-Metadaten)

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| `intent_aliases` | (string \| {alias, arguments})[] | Router matcht nur exakt normalisierte Aliase — keine Volltextmagie |
| `example_prompts` | string[] | Doku/Diagnostik |
| `negative_examples` | string[] | verhindern Fehlrouting bei write-lastigen/mehrdeutigen Prompts |
| `confidence_hint` | number | Router-Confidence für Alias-Matches |
| `env_allowlist` | string[] | einzige Env-Variablen, die der Executor durchreicht |
| `path_arguments` | string[] | Argumente, die als Pfade gegen `allowed_paths`/`denied_paths` validiert werden |
| `interpreter` | enum | `bash` \| `python3` (nur `execution_kind=script`) |

Nutzungs-Metadaten (`last_used`, `success_count`, `fail_count`,
`last_failure_reason`) leben nicht im Proposal, sondern im
`dynamic_tool_record.v1` der Registry und werden vom Executor
aktualisiert.

## Test-Case-Format (HDE-017)

```json
{
  "name": "counts todos",
  "kind": "positive",
  "setup_files": {"a.py": "# TODO eins\n"},
  "arguments": {"path": "a.py"},
  "expect_exit_code": 0,
  "expect_status": "ok",
  "expect_output_contains": ["1"],
  "expect_output_not_contains": ["error"],
  "expect_changed_paths": []
}
```

Validation läuft in einem isolierten Temp-Workspace; der Report wird
als `custom_tool_validation_report.v1` unter
`tool-proposals/reports/<digest>.json` gespeichert und im Proposal
referenziert.

## Digest & Persistenz

- `proposal_digest` = SHA-256 über das kanonische Proposal ohne
  Lifecycle-Felder (`status`, `approval_status`, Refs, Timestamps).
- Persistenz: `<data_root>/tool-proposals/<digest>.json`
  (Default-`data_root`: `<DATA_DIR>/custom-tools`).
- Duplikate (gleicher Digest) werden erkannt und nicht erneut angelegt.
- Jede inhaltliche Änderung erzeugt einen neuen Digest und invalidiert
  damit alte Validation-Reports und Approval-Grants (HDE-015).
