# Artifact-First Worker Handoff Architecture

## Summary

The Hub must treat **files, artifact manifests, and verification evidence** as the primary source of truth for task completion. LLM chat output (including JSON in the final response) is **advisory only** and must never be the sole authority for marking a task completed or triggering a retry loop.

## Problem Statement

The observed failure mode: a worker generated the expected files correctly (Fibonacci Flask project: `app.py`, `requirements.txt`, `README.md`), but the model returned Markdown/natural language instead of strict JSON as its final response. The Hub remained stuck in a retry loop because it waited for valid JSON — even though the real work was done.

---

## Flow: Hub → Handoff → Worker → Manifest → Collector → Completion

```
Hub
 ├─ WorkerHandoffService.create_handoff()
 │   └─ writes .ananta/handoff/<execution_id>/
 │       ├─ worker_handoff.json        (WorkerHandoffBundle v1)
 │       ├─ instructions.md            (includes manifest requirement)
 │       ├─ expected_artifacts.json
 │       └─ completion_policy.json
 │
Worker executes
 ├─ reads instructions.md
 ├─ produces app.py, requirements.txt, README.md
 └─ writes .ananta/handoff/<execution_id>/artifact_manifest.v1.json
     (final chat response = summary only, never authoritative)
 │
Hub (after execution)
 ├─ WorkspaceDiffService.take_after_snapshot()   [optional fallback]
 ├─ WorkerOutputCollectorService.collect()
 │   └─ reads artifact_manifest.v1.json
 │   └─ validates schema + hashes + workspace boundary
 │   └─ falls back to synthesized manifest from diff if policy allows
 │
 ├─ TaskCompletionPolicyService.evaluate()
 │   ├─ inputs: collection_result, advisory_parse_result, exit_code
 │   ├─ advisory_parse_result from parse_followup_analysis() — NEVER authoritative
 │   └─ decision: completed | needs_review | retryable_failed | failed
 │
 └─ TaskFinalizationService.finalize_from_artifacts()
     ├─ emits audit events: artifact_manifest_collected, artifact_completion_decided, ...
     └─ updates task status via task_runtime_service
```

---

## Key Contracts

### ArtifactManifest v1 (`schemas/artifacts/artifact_manifest.v1.json`)

Written by the **worker** to `.ananta/handoff/<execution_id>/artifact_manifest.v1.json`.

Required fields:
- `schema: "artifact_manifest.v1"`
- `manifest_id`, `goal_id`, `task_id`, `execution_id`, `trace_id`
- `workspace_root_ref` (hash — never absolute path)
- `produced_by_worker_id`, `produced_at`
- `artifacts[]` — each with `artifact_id`, `kind`, `relative_path`, `content_hash`, `size_bytes`

Security:
- `relative_path` must never start with `/` or contain `..`
- Hub validates workspace boundary before trusting paths

### WorkerHandoffBundle v1 (`schemas/worker/worker_handoff_bundle.v1.json`)

Written by the **Hub** before worker execution. Tells the worker:
- What files to produce (`expected_artifacts`)
- Where to write the manifest (`manifest_output_path`)
- The completion policy (`completion_policy_ref`)

Key instruction appended to `instructions.md`:
> After completing all tasks, you MUST write the artifact manifest to `<manifest_output_path>`.
> The Hub uses this file — not your chat response — to confirm task completion.
> Your final chat response is a summary only.

### ArtifactCompletionPolicy v1 (`schemas/artifacts/artifact_completion_policy.v1.json`)

Deterministic Hub-side policy. Key fields:
- `required_paths`: paths that must exist and be verified
- `verification_required`: whether artifact hashes must be confirmed
- `allow_synthesized_manifest`: whether workspace-diff fallback is allowed
- `max_retries`: bounded retry budget

---

## Advisory-Only Model Output

`parse_followup_analysis()` in `planning_utils.py` is **always advisory**:
- Returns `advisory: True` always
- Returns `task_complete: None` when JSON is malformed (not `True`)
- A `reason_code: advisory_parse_failed_ignored` is logged when parse fails but artifacts pass
- **Callers must never drive completed-state from `task_complete` alone**

`worker_todo_planner_service.py` LLM refinement:
- `planner_llm_enabled` defaults to **`False`**
- When enabled, LLM output is wrapped in `PlannerProposalArtifact` — never directly replaces tasks
- Malformed LLM output creates proposal with `parse_status: failed/markdown_fenced/natural_language`
- Deterministic contract is always returned as fallback

---

## Retry Policy

`TaskRetryPolicyService` distinguishes:

| Reason | Classification | Retry? |
|--------|---------------|--------|
| `planner_llm_parse_failed` + deterministic contract exists | `non_retryable` | No |
| `advisory_json_parse_failed` + valid artifacts | `ignored` | No |
| `missing_required_artifact` (< max) | `retryable` | Yes |
| `verification_failed` | `needs_review` | No |
| `worker_execution_failed` (< max) | `retryable` | Yes |
| Any at max retries | `non_retryable` | No |

**Critical:** advisory parse failure with valid artifacts must never cause a retry loop.

---

## Migration Bridge: Synthesized Manifests

For workers that don't yet write manifests:
1. `WorkspaceDiffService` snapshots workspace before/after execution
2. Synthesizes a manifest from created/modified files
3. Marks it `synthesized: true` (lower trust)
4. `ArtifactCompletionPolicy.allow_synthesized_manifest=false` by default — must be opt-in

---

## Legacy JSON Planner Mode

`planner_llm_enabled=true` is explicitly **opt-in** via config:
```yaml
worker_runtime:
  todo_contract:
    planner_llm_enabled: true  # NOT the default
    provider: lmstudio
    model: my-model
```

Even when enabled:
- LLM output → `PlannerProposalArtifact` (advisory, adoption_status=pending)
- Deterministic contract is always the fallback
- Proposal adoption requires deterministic validation

---

## Example: Fibonacci Flask Project Manifest

```json
{
  "schema": "artifact_manifest.v1",
  "manifest_id": "mfst-abc123",
  "goal_id": "goal-fibonacci",
  "task_id": "task-fibonacci",
  "execution_id": "exec-fibonacci",
  "trace_id": "tr-fibonacci",
  "workspace_root_ref": "a1b2c3d4",
  "produced_by_worker_id": "ananta-worker-1",
  "produced_at": 1747000000.0,
  "summary": "Fibonacci Flask project with REST API",
  "synthesized": false,
  "artifacts": [
    {
      "artifact_id": "art-001",
      "kind": "generated_file",
      "relative_path": "app.py",
      "content_hash": "sha256...",
      "size_bytes": 312,
      "classification": "internal",
      "operation": "created",
      "required": true,
      "verification_status": "pending"
    },
    {
      "artifact_id": "art-002",
      "kind": "generated_file",
      "relative_path": "requirements.txt",
      "content_hash": "sha256...",
      "size_bytes": 12,
      "required": true
    },
    {
      "artifact_id": "art-003",
      "kind": "generated_file",
      "relative_path": "README.md",
      "content_hash": "sha256...",
      "size_bytes": 128,
      "required": true
    }
  ]
}
```

---

## Audit Events

All artifact-first transitions emit audit events (see `agent/common/audit.py`):

- `worker_handoff_created`
- `artifact_manifest_collected`
- `artifact_manifest_synthesized`
- `artifact_completion_decided`
- `task_finalized_from_artifacts`
- `advisory_json_parse_failed_ignored`
- `artifact_reconciliation_applied`

Manual reconciliation via `ArtifactReconciliationService` requires `actor` + `reason` and emits `artifact_reconciliation_applied`.

---

## Non-Goals

- This architecture does **not** remove Hub-generated JSON contracts (schemas remain required)
- Strict JSON is still valid for Hub-built contracts and manifests — only model chat JSON is demoted
- Worker chat response is preserved as a summary/advisory artifact — it is just not authoritative
