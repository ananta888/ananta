# Capability Vocabulary and Risk Classes

**Track:** EW-T005  
**Date:** 2026-05-10

Canonical list of capability classes used by Hub policy, ToolRouter, Worker validation, and Approval flow.  
Unknown capability class → denied, not treated as low risk.

---

## Vocabulary

| Capability class | Risk class | Approval default | Side-effect type | Context requirements | Audit requirements |
|---|---|---|---|---|---|
| `planning` | low | allow | none | task description | trace event |
| `research` | low | allow | none | search context | trace event |
| `code_read` | low | allow | none | filesystem scope | trace event |
| `patch_propose` | medium | allow | artifact (PatchArtifact) | filesystem scope | trace event + artifact |
| `patch_apply` | high | confirm_required | filesystem mutation | filesystem scope + approval ref | trace event + artifact + audit log |
| `shell_plan` | low | allow | artifact (CommandPlanArtifact) | workspace scope | trace event |
| `shell_execute` | high | confirm_required | host mutation | workspace scope + approval ref | trace event + artifact + audit log |
| `test_run` | medium | allow | output only | workspace scope | trace event + artifact |
| `verify` | low | allow | none | artifact refs | trace event |
| `memory_read` | low | allow | none | memory scope ref | trace event |
| `memory_write` | medium | confirm_required | persistent state | memory scope ref + approval ref | trace event + audit log |
| `mcp_call` | medium | confirm_required | external side effects | mcp tool id | trace event + artifact |
| `provider_call` | medium | allow | external call | model policy | trace event |
| `subworker_spawn` | high | confirm_required | delegated execution | sub-envelope ref | trace event + audit log |
| `cron_schedule` | high | confirm_required | scheduled mutation | job definition | trace event + audit log |
| `artifact_publish` | low | allow | artifact | artifact ref | trace event |

---

## Risk class definitions

| Risk class | Description |
|---|---|
| `low` | Read-only or additive; no host or persistent state mutation |
| `medium` | Produces artifacts or calls external services; limited blast radius |
| `high` | Mutates host state, persistent memory, or schedules work; requires approval |
| `critical` | Privilege escalation or unrecoverable mutation; blocked by default in all profiles |

---

## Approval defaults

| Approval default | Meaning |
|---|---|
| `allow` | Preflight passes without an `ApprovalRef` for this capability |
| `confirm_required` | Preflight requires a matching `ApprovalRef`; absent → `needs_approval` |
| `deny` | Never allowed regardless of approval refs |

---

## Unknown capability

If the `ExecutionEnvelope` contains a capability class not in this vocabulary, the preflight gate returns `missing_capability` and denies the entire execution.  
Unknown capabilities are **never** treated as low risk or silently ignored.

---

## Consumption

Hub policy uses `risk_class` to determine governance profile thresholds.  
Worker preflight uses `approval_default` to decide whether an `ApprovalRef` is mandatory.  
ToolRouter uses `capability_classes` in `WorkerToolRegistry` entries to map tools to capabilities.
