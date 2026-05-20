# Hermes Free Model Routing

This setup introduces task-kind-aware Hermes model selection for read-only tasks.

## Purpose

Hermes should handle only governed, non-mutating work:
- `plan_only`
- `summarize`
- `review`
- `patch_propose`

Hermes must not execute mutating tasks like `patch_apply`, `command_execute`, shell execution, or workspace mutation.

## Configuration

Use `hermes_worker_adapter` with:
- `task_kind_models`
- `fallback_free_models`
- `model_selection_policy`

Example:

```json
{
  "default_model": "z-ai/glm-4.5-air:free",
  "task_kind_models": {
    "plan_only": "z-ai/glm-4.5-air:free",
    "summarize": "z-ai/glm-4.5-air:free",
    "review": "qwen/qwen3-coder:free",
    "patch_propose": "qwen/qwen3-coder:free"
  },
  "fallback_free_models": {
    "plan_only": ["z-ai/glm-4.5-air:free"],
    "review": ["qwen/qwen3-coder:free"],
    "default": ["moonshotai/kimi-k2:free"]
  },
  "model_selection_policy": {
    "prefer_task_specific_model": true,
    "require_free_model_suffix": true,
    "allow_fallback_on_unavailable": true,
    "reject_mutation_tasks_for_hermes": true
  }
}
```

## patch_propose vs patch_apply

- `patch_propose`: read-only proposal artifact (diff/text), no execution.
- `patch_apply`: mutating operation, blocked for Hermes.

## Why `:free` is policy-based

`require_free_model_suffix` is optional for backward compatibility. Enable it per profile/scenario where free-tier routing is required.

## Env Vars

- `OPENROUTER_API_KEY`
- `HERMES_BASE_URL`
- `OPENCODE_ENABLED`
- `OPENCODE_TEST_MODEL`

## Pytest Commands

Offline:

```bash
pytest -q tests/test_hermes_model_selection_service.py tests/test_hermes_free_models_scenario.py tests/test_hermes_free_model_routing.py tests/test_hermes_free_models_read_only.py tests/test_hermes_opencode_small_project_flow.py
```

Live smoke (optional):

```bash
pytest -q tests/test_hermes_free_models_live_smoke.py
```
