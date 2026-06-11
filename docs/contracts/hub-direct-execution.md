# Contract: Hub-Direct-Execution (`hub_direct_execution_decision.v1` / `hub_direct_execution_result.v1`)

Track: `todos/todo.hub-direct-execution-dynamic-tools.json` (HDE-004),
amended by `todos/todo.hub-direct-execution-worker-runtime-amendment.json`.

## Grundsatz: Control Plane vs. Execution Plane (HDW-001)

**Der Hub ist Control Plane, die WorkerRuntime ist Execution Plane.**

- Der Hub **entscheidet** (Router), **autorisiert** (Policy-Gate,
  Approval-Lifecycle), **auditiert** und **dispatcht**.
- Der Hub führt niemals Tool-, Shell-, Test- oder Script-Logik in
  seinem eigenen Prozess aus. Die Ausführung erfolgt über den
  `WorkerRuntimeExecutionAdapter`
  (`agent/services/worker_runtime_execution_adapter.py`) in einer
  WorkerRuntime/Sandbox mit explizitem `workspace_ref`, Timeout,
  Output-Limits, Env-Allowlist und MutationGate (HDW-004).

```
User/Task --> HubDirectRouter --> Policy/Approval --> WorkerRuntime --> ToolResult --> Hub --> Response
                  |                     |                                                |
                  | eligible=false      | policy_blocked / approval_required             | Evidence/Audit
                  v                     v                                                v
            Worker/LLM-Fallback   direct_policy_blocked /                       hub_direct_tool_completed
                                  direct_approval_required
```

## Abgrenzung zu `ananta_worker_tool_loop.v1`

| Aspekt | Hub-Direct-Execution | ananta-worker Tool-Loop |
| --- | --- | --- |
| Auslöser | Deterministische Router-Regeln vor dem LLM | ToolRequests eines laufenden Worker-LLM |
| LLM beteiligt | Nein (`requires_llm=false`) | Ja (Worker-LLM fordert Tools an) |
| Registry | dieselbe (`ananta_tool_registry_service`) via `HubToolExecutionAdapter` | dieselbe, direkt im Loop |
| Policy | identisches Gate (`AnantaToolPolicyService`) | identisches Gate |
| Ausführung | Dispatch an WorkerRuntime | Worker-Runtime des Loops |

Beide Pfade teilen Registry, Policy, Approval und Audit — es gibt keinen
zweiten unkontrollierten Ausführungspfad (HDE-DD-002).

## Execution Plane pro Toolklasse (HDW-003)

Jedes `AnantaToolSpec` trägt `execution_plane`:

| Plane | Bedeutung | Beispiele |
| --- | --- | --- |
| `worker_runtime` | läuft in der WorkerRuntime mit Workspace-Scope | `repo.*`, `git.*`, `test.*`, `workspace.*`, `shell.run_allowlisted` |
| `sandbox_runtime` | wie worker_runtime, zusätzlich isolierte Sandbox | `custom.*`/`project.*` (wählbar) |
| `external_backend` | Anfrage an externen Backend-/Index-Dienst | `codecompass.*`, `opencode.propose`, `hermes.review` |
| `hub_control_only` | reine Metadaten-/Registry-Leseoperation ohne Workspace/Shell | Registry-Snapshots |

Regeln:

- `repo.*`, `git.*`, `test.*`, `workspace.*` und `custom.*` mit
  Workspace-Zugriff laufen **nie** im Hub-Prozess.
- Tools ohne bekannte `execution_plane` blockt das Policy-Gate
  (`rule_id=execution_plane_gate`).
- `custom.*`/`project.*` ohne `execution_plane` in
  `{worker_runtime, sandbox_runtime}` sind nicht aktivierbar
  (Schema-Validation im Proposal + Policy-Gate).

## Decision-Schema: `hub_direct_execution_decision.v1`

Erzeugt von `HubDirectExecutionRouter.classify(prompt, task, agent_cfg)`:

```json
{
  "schema": "hub_direct_execution_decision.v1",
  "eligible": true,
  "tool_name": "repo.grep",
  "arguments": {"pattern": "ToolRoutingService", "limit": 50},
  "reason_code": "deterministic_rule_match",
  "confidence": 0.85,
  "requires_llm": false,
  "source": "static"
}
```

- `eligible=false` ⇒ `requires_llm=true`; `tool_name`/`arguments` leer.
- `source` ist `static` (Registry-Regel) oder `dynamic`
  (Intent-Alias eines aktiven Custom Tools, HDE-014).
- `arguments` sind strikt begrenzt und enthalten nie rohe
  Shell-Kommandos.

`reason_code`-Werte (nicht abschließend): `deterministic_rule_match`,
`custom_tool_intent_alias_match`, `hub_direct_disabled`,
`empty_prompt`, `prompt_too_long`, `mutation_or_complex_intent`,
`no_rule_match`, `tool_not_in_allowed_tools`,
`below_confidence_threshold`.

## Result-Schema: `hub_direct_execution_result.v1`

Erzeugt vom `HubToolExecutionAdapter` (Control Plane). `kind` ist eines
von:

| kind | Bedeutung |
| --- | --- |
| `direct_tool_call` | (implizit) der autorisierte Dispatch an die WorkerRuntime; auditierbar als `hub_direct_tool_requested` + `worker_runtime_dispatch` |
| `direct_tool_result` | Ausführung abgeschlossen; `tool_result` ist ein `ananta_tool_result.v1` |
| `direct_not_eligible` | Router lehnt ab; Worker/LLM-Fallback oder explizite Ablehnung |
| `direct_policy_blocked` | Policy-Gate blockt; **kein** stiller LLM-Fallback |
| `direct_approval_required` | Pending `ApprovalRequest` (digest-gebunden) wurde erzeugt |

```json
{
  "schema": "hub_direct_execution_result.v1",
  "kind": "direct_tool_result",
  "tool_name": "repo.grep",
  "tool_result": {"schema": "ananta_tool_result.v1", "...": "..."},
  "policy_decision": {"decision": "allow", "...": "..."},
  "task_id": "...",
  "goal_id": null
}
```

Verbindliche Regeln:

- **Direct Execution darf kein LLM-Ergebnis vortäuschen.** Antworten
  referenzieren ausschließlich ToolResults/Evidence
  (`ananta_tool_result.v1`); `cost_summary.provider/model` sind `None`,
  `tokens_total=0`, es entsteht kein `llm_call_profile`.
- **Fallback-Regel:** Wenn eine Aufgabe nicht sicher deterministisch
  beantwortbar ist (`eligible=false`, recoverable Tool-Failure), wird
  der Worker/LLM verwendet (`hub_direct_fallback_to_worker`). Ein
  Policy-Block oder Approval-Pending fällt **nicht** still auf das LLM
  zurück.
- **Approval:** `direct_approval_required` erzeugt einen pending
  `ApprovalRequest` mit `scope.source=hub_direct_execution` und
  digest-gebundenen Argumenten. Nach Grant wird nur der exakt gleiche
  `arguments_digest` ausgeführt; One-Shot-Grants werden nach
  erfolgreicher Ausführung konsumiert.

## Audit-Events (HDE-009 / HDW-005)

`hub_direct_candidate_detected`, `hub_direct_tool_requested`,
`hub_direct_tool_completed`, `hub_direct_tool_blocked`,
`hub_direct_approval_required`, `hub_direct_fallback_to_worker`,
`worker_runtime_dispatch`. Payloads enthalten `task_id`, `goal_id`,
`tool_name`, `policy_decision`, `risk_class`, `reason_code` — niemals
Secrets, rohe Prompts oder vollständige Outputs. Audit-Fehler brechen
die Ausführung nicht ab.
