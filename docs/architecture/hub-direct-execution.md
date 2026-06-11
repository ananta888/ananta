# Architektur: Hub-Direct-Execution & Dynamic Tools

Tracks: `todos/todo.hub-direct-execution-dynamic-tools.json`,
`todos/todo.hub-direct-execution-worker-runtime-amendment.json`.

## Drei Ausführungspfade, eine Kontrollschicht

| Pfad | Auslöser | LLM | Ausführung |
| --- | --- | --- | --- |
| **Hub-Direct-Execution** | deterministische Router-Regeln vor dem Worker | nein | Dispatch an WorkerRuntime |
| **ananta-worker Tool-Loop** | ToolRequests eines Worker-LLM (`agent/common/sgpt_tool_loop.py`) | ja | Worker-Runtime des Loops |
| **klassische `tool_calls`/`command`** (`TaskExecutionService.execute_local_step`) | explizite API-Clients | optional | bestehender Pfad, unverändert |

Alle drei teilen Policy-Gate (`AnantaToolPolicyService`),
Approval-Lifecycle (`ApprovalRequestService`), MutationGate und Audit.
Hub-Direct nutzt die ananta-worker-Registry über den
`HubToolExecutionAdapter` — keine doppelten Tooldefinitionen mit
`agent.tools.registry` (HDE-DD-006).

## Control Plane / Execution Plane (Amendment HDW)

**Der Hub entscheidet, autorisiert, auditiert und dispatcht — die
WorkerRuntime führt aus.** (HDW-DD-001)

```
User/Task
   |
   v
HubDirectExecutionRouter.classify()          [Hub, deterministisch]
   | eligible=false -> Worker/LLM-Fallback (hub_direct_fallback_to_worker)
   v
HubToolExecutionAdapter                      [Hub = Control Plane]
   |-- AnantaToolPolicyService.evaluate()    allow | approval_required | policy_blocked
   |-- ApprovalRequestService                pending ApprovalRequest (digest-gebunden)
   |-- Audit (hub_direct_*)
   v
WorkerRuntimeExecutionAdapter.dispatch()     [Execution Plane]
   |-- expliziter workspace_ref (nie Hub-Arbeitsordner)
   |-- Env-Allowlist, Output-Limits, Timeout
   v
WorkerRuntime (local_process | sandbox | remote)
   |-- statische Tools: deterministische Executors (agent/services/tools)
   |-- custom.*: CustomToolExecutor (Sandbox-Grenzen + MutationGate)
   v
ananta_tool_result.v1  ->  Hub korreliert Evidence  ->  Response
```

Entscheidung zur bestehenden Runtime-Infrastruktur (HDW-002):
`NativeWorkerRuntimeService` bleibt die Command-Plan-Runtime für
Worker-LLM-Flows. Der `WorkerRuntimeExecutionAdapter` ist die
Tool-Execution-Plane für Hub-Direct; sein Default-Backend
(`LocalProcessWorkerRuntime`) ist die gleiche lokale Prozessgrenze, in
der heute auch der Worker-Tool-Loop ausführt. Docker-/Remote-Targets aus
`worker_runtime_target_service`/`worker_runtime_selection_service`
können als weitere Backends eingehängt werden, ohne die Control Plane
zu ändern. Wenn ein nicht-lokales Runtime-Target konfiguriert ist, aber
kein Transport-Backend verfügbar ist, blockt der Dispatch fail-closed
mit `worker_runtime_backend_unavailable`; es gibt keinen stillen
Fallback auf den Hub-Arbeitsordner oder lokale Shell-Ausführung.

## Dynamic-Tool-Lifecycle

```
LLM/User-Vorschlag
   v
CustomToolProposalService            tool_proposal.v1, status=pending, digest
   v
CustomToolValidationService          Tests in isoliertem Temp-Workspace
   v  validated (Report-Artefakt)
CustomToolPromotionService           ApprovalRequest (digest-gebunden)
   v  approved
DynamicToolRegistryService           dynamic_tool_record.v1, status=active
   v
Reuse: HubDirectRouter (nur Intent-Aliase) / Worker-Prompt (describe_for_prompt)
```

- Statische Registry hat Vorrang; `custom.*` darf nichts überschreiben.
- Disable löscht nichts; Rollback nur auf validierte+approved Versionen.
- Usage-Metadaten (success/fail counts) pflegt der Executor.

## Konfiguration

`hub_direct_execution` in `agent/config_defaults.py` — `enabled=false`
als sicherer Default. Felder: `direct_before_worker`,
`fallback_to_worker`, `require_policy_gate`, `audit_enabled`,
`confidence_threshold`, `max_result_chars`, `allowed_tools`.

## Diagnostik

- `GET /api/diagnostics/hub-direct/config|metrics|registry`
- `GET/POST /api/custom-tools/...` (Promotion-Aktionen nur Admin)
- Operator-TUI-Ansicht: **TODO** — bewusst noch nicht umgesetzt; die
  API-Endpunkte oben sind die Datenquelle dafür.

## Metriken (HDE-021)

`direct_execution_count`, `direct_execution_success_count`,
`direct_execution_blocked_count`, `fallback_to_worker_count`,
`avoided_llm_call_count`, `custom_tool_reuse_count` — in-process,
abrufbar über die Diagnostik-API; nur Tool-Namen und Reason-Codes,
keine Prompts/Outputs.

## Rollout

1. `hub_direct_execution.enabled=false` bleibt der sichere Default.
2. Read-only Direct Tools (`repo.*`, `git.status`, `test.discover`)
   zuerst pro Profil aktivieren und Metriken/Audit prüfen.
3. Dynamic Tool Promotion nur mit Admin-Approval einschalten; Script
   Tools müssen `script_body_digest` tragen und Validation bestehen.
4. Für schreibende oder scriptbasierte Tools eine WorkerRuntime mit
   explizitem `workspace_ref` und passender `execution_plane` erzwingen.
5. Nicht-lokale Runtime-Targets erst produktiv aktivieren, wenn das
   konkrete Docker-/Remote-Backend verfügbar ist; bis dahin fail-closed.
