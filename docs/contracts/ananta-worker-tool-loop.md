# Contract: ananta-worker Tool Loop (`ananta_worker_tool_loop.v1`)

AWTCL-002. Dieser Vertrag definiert das JSON-Protokoll zwischen dem
ananta-worker-LLM und dem Hub im Tool-Calling-Loop
(`agent/common/sgpt_tool_loop.py`). Er ist provider-neutral: natives
Function Calling ist nicht erforderlich (AWTCL-DD-002).

## Grundregeln

- Das Worker-LLM antwortet pro Iteration mit **genau einem JSON-Objekt** —
  zulässig sind **rohe JSON-Antworten** und **fenced JSON**
  (` ```json ... ``` `). Alles andere gilt als ungültige Antwort und fällt
  nach `max_invalid_outputs` kontrolliert auf eine Textantwort zurück.
- Das LLM führt **nie** selbst Tools aus. Es fordert sie an; der Hub
  validiert (Registry + Policy Gate) und führt deterministisch aus
  (AWTCL-DD-001/DD-003).
- Der Hub bleibt finaler Entscheider; `approval_required` wird nie
  automatisch zu `allow`.
- Für Brownfield-Codeänderungen gilt patch-first/range-first:
  `codecompass.plan_context` oder `repo.grep` → `repo.read_file_range` →
  `patch_request` → `workspace.diff` → `test.run`. Vollständige
  Datei-Rewrites sind Ausnahmefälle.

## LLM-Outputs (`kind`)

### `tool_request`

```json
{
  "kind": "tool_request",
  "tool_name": "repo.grep",
  "reason": "Need deterministic search for ToolRoutingService usage.",
  "arguments": {
    "pattern": "ToolRoutingService",
    "path_globs": ["agent/**/*.py", "worker/**/*.py"],
    "limit": 50,
    "context_before": 2,
    "context_after": 2
  },
  "risk_hint": "read"
}
```

Für CodeCompass-gestützte Brownfield-Arbeiten ist
`codecompass.plan_context` der bevorzugte erste ToolRequest:

```json
{
  "kind": "tool_request",
  "tool_name": "codecompass.plan_context",
  "arguments": {
    "query": "Task approval flow",
    "max_ranges": 8,
    "include_neighbors": true,
    "task_kind": "bugfix"
  }
}
```

- `tool_name` (Pflicht): registrierter Toolname aus
  `agent/services/ananta_tool_registry_service.py`.
- `arguments` (Pflicht, Objekt): Argumente gemäß `argument_schema` des Tools.
- `reason` / `risk_hint` (optional): Begründung und Selbsteinschätzung.
- Die Korrelation läuft über den Hub: der n-te ausgeführte ToolCall einer
  Session erhält die `tool_call_id` `tool_result:<n>`; das LLM referenziert
  sie in `evidence_refs`.

### `final_answer`

```json
{
  "kind": "final_answer",
  "answer": "…",
  "evidence_refs": ["tool_result:1", "tool_result:3"]
}
```

Beendet den Loop. Aussagen sollen über `evidence_refs` auf gelieferte
ToolResults verweisen.

### `needs_approval`

```json
{ "kind": "needs_approval", "reason": "git push erforderlich" }
```

Beendet den Loop; der Hub meldet das Approval-Bedürfnis strukturiert nach
oben. Es findet keine Ausführung statt.

**ALWA-007 + ALWA-009**: `needs_approval` (ebenso wie `KIND_NEEDS_APPROVAL`
im sgpt_workspace_mutation-Pfad und `approval_required` im
Tool-Policy-Gate) registriert hub-seitig einen
`ApprovalRequestDB(status=pending)` mit `tool_name`,
`arguments_digest` (sha256 über kanonisierte Argumente +
`target_fingerprint`) und `scope` (keine rohen Prompts). Der Task geht
auf `pending_approval` / `blocked_pending_approval`, der Loop endet
**kontrolliert** — kein Busy-Wait. Nach `granted`-Decision via
`POST /api/approvals/<id>/decision` re-dispatcht der Hub den Task
(status=todo, reason=approval_granted_redispatch, audit
`approval_request_redispatch`). **Grants sind digest-gebunden**: ein
Grant für `repo.write_file path=foo.txt content=hash-A` deckt nicht
denselben Call mit `content=hash-B` ab — `arguments_digest` muss
exakt passen.

### `cannot_continue_without_context`

```json
{ "kind": "cannot_continue_without_context", "reason": "Branch-Historie fehlt" }
```

Beendet den Loop, wenn deterministische Daten fehlen und kein freigegebenes
Tool sie liefern kann. Nicht raten — dieses Signal verwenden.

## ToolResult (`ananta_tool_result.v1`)

```json
{
  "schema": "ananta_tool_result.v1",
  "tool_call_id": "tool_result:1",
  "tool_name": "repo.grep",
  "status": "ok",
  "risk_class": "read",
  "evidence": [
    {
      "kind": "grep_match",
      "path": "agent/services/tool_routing_service.py",
      "line_start": 11,
      "line_end": 20,
      "excerpt": "…",
      "truncated": false
    }
  ],
  "warnings": [],
  "error": null,
  "policy_decision": {
    "decision": "allow",
    "reason": "read_only_in_scope",
    "rule_id": "read_only_allowed",
    "risk_class": "read",
    "policy_version": "ananta-tool-policy-v1"
  }
}
```

- `status`: `ok` | `error` | `test_failed` | `rejected` | `policy_blocked` |
  `approval_required` | `invalid_output`.
- `evidence`: begrenzte Einträge (Datei-Line-Ranges, Snippets, Graphpfade,
  Testausgaben). Große Ergebnisse werden gekürzt und mit
  `evidence_truncated` in `warnings` markiert (AWTCL-007).
- `policy_decision`: Entscheidung des Policy Gates — auch geblockte und
  approval-pflichtige Requests erzeugen einen ToolResult, damit das LLM den
  Grund sieht.

## CodeCompass ContextBundle

`codecompass.plan_context` liefert `data.context_bundle` mit
`schema=codecompass_context_bundle.v1`. Das Bundle enthält begrenzte
`location_refs` (`path`, `line_start`, `line_end`, `symbol`, `reason`,
`score`, `source`) und daraus abgeleitete `patch_targets`. Der
Mutation-Loop kann die Top-Ranges unmittelbar über `repo.read_file_range`
materialisieren; große RAG-Volltextblöcke sollen dadurch nicht in den
Prompt gelangen.

## Loop-Verhalten

| Bedingung | Verhalten |
|---|---|
| `final_answer` | Loop endet, Antwort wird zurückgegeben |
| `max_iterations` erreicht | kontrollierter Abbruch (`loop_aborted`/`max_iterations_reached`) |
| `max_tool_calls` erreicht | kontrollierter Abbruch (`max_tool_calls_reached`) |
| `max_invalid_outputs` ungültige Antworten | Fallback auf rohe Textantwort |
| `needs_approval` / `cannot_continue_without_context` | Loop endet mit strukturierter Meldung |

Konfiguration: `ananta_worker_tool_loop` in `agent/config_defaults.py`
(`enabled`, `max_iterations`, `max_tool_calls`, `max_tool_result_chars`,
`max_invalid_outputs`, `allowed_tools`). Bei deaktiviertem Flag läuft der
bestehende Kontext-Batch-Loop (`_run_ananta_worker_iterative`) unverändert
weiter (AWTCL-011).

## Diagnostik & Audit

- Pro Run schreibt der Loop `.ananta/tool-loop-report.json`
  (Iterationen, Policy-Entscheidungen, Outcome) — sichtbar über
  `GET /api/diagnostics/ananta-worker/runs|report` und die UI-Seite
  „Worker Loop Diagnostik".
- Jeder ToolRequest/ToolResult wird auditiert
  (`ananta_worker_tool_requested|completed|blocked|approval_required`,
  siehe `agent/common/audit.py`); Secrets und lange Outputs werden vor dem
  Audit redigiert/gekürzt (AWTCL-008).
- **ALWA-009 + ALWA-019**: Approval-Lifecycle-Events
  (`approval_request_created|decided|consumed|expired|superseded|redispatch`
  + `approval_legacy_bypass_used`) sind in `agent/common/audit.py` und
  `agent/services/approval_request_service.py` definiert. Die
  Approval-UI zeigt nur **Digest-Prefix** und **scope_summary**, niemals
  rohe Argumente. Vollständige Quelle: `docs/security/approval-lifecycle.md`.
