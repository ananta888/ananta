# agent.cli_backends — LLM-CLI Backend Subsystem Architecture

**Track:** SGDEC
**Status:** Welle 1+2+3 complete. `agent.cli_backends.*` is the source of
truth; the legacy `agent.common.sgpt_*` shim layer is deleted.
**SOLID-Bezug:** SRP (`workspace_mutation` split), DIP
(`CliBackendContext` service boundary), OCP (new backends live under
`agent/cli_backends/`).

## 1. Subsystem Overview

`agent.cli_backends` is the public API and implementation namespace for
the LLM-CLI backend subsystem: sgpt, opencode, codex, aider and mistral.
Production code imports from this namespace. The detector
`scripts/check_cli_backend_shim_imports.py` rejects legacy
`agent.common.sgpt_*` imports.

### 1.1 Modul-Map

| Module | Responsibility | Status |
|--------|----------------|--------|
| `context.py` | `CliBackendContext` service boundary | Source-of-truth |
| `helpers.py` | backend config/runtime helpers | Source-of-truth |
| `semaphore.py` | backend concurrency limits | Source-of-truth |
| `routing.py` | backend capability/routing tables | Source-of-truth |
| `sgpt.py` | sgpt command integration | Source-of-truth |
| `tool_loop.py` | hub-controlled worker tool loop | Source-of-truth |
| `opencode.py` | opencode/codex/aider/mistral runners | Source-of-truth |
| `architecture_scan.py` | architecture scan helpers | Source-of-truth |
| `workspace_mutation/` | workspace mutation loop package | Source-of-truth |

### 1.2 Service-Locator-Pattern (DIP)

`agent.cli_backends.context.CliBackendContext` is the only service
resolution boundary inside this subsystem. Backend modules use
`default_context.<service_or_adapter>` instead of direct
`agent.services.*` imports.

```python
from agent.cli_backends.context import default_context

policy = default_context.ananta_tool_policy_service
workspace = default_context.worker_workspace_service
execute_tool = default_context.ananta_tool_executor
```

### 1.3 Workspace-Mutation Split (SGDEC-T04)

`agent.cli_backends.workspace_mutation` is a package with focused helper
modules:

- `signatures.py`: stable hashes for evidence and changed file sets
- `prompts.py`: mutation JSON parsing and prompt construction
- `loop.py`: public config/run entry points
- `tools.py`: tool execution, evidence and source-line-policy adapters
- `_orchestrator.py`: the bounded orchestration loop implementation

The remaining orchestrator is intentionally isolated behind the package
API. Further decomposition should keep hub ownership intact: the hub
validates workspace writes, policy checks and tool execution; workers
only request delegated actions.

## 2. Cross-Cutting Decision (SGDEC-D5)

The following modules remain in `agent.common.*` because they are
cross-cutting facades rather than LLM-CLI backend modules:

- `agent.common.audit`
- `agent.common.error_handler`
- `agent.common.signals`

Moving them would affect many unrelated subsystems and is tracked
separately in `todo.common-layer-deepen-services.md`.

## 3. Detector (SGDEC-T06)

`scripts/check_cli_backend_shim_imports.py` is the legacy import gate:

- exit 0: no legacy `agent.common.sgpt_*` imports remain
- exit 1: one or more legacy imports must be migrated
- `--json`: machine-readable output for CI

## 4. Tests

| Test file | Purpose |
|-----------|---------|
| `test_cli_backend_namespace_skeleton.py` | package and module import surface |
| `test_cli_backend_context_injection.py` | lazy, overrideable `CliBackendContext` |
| `test_cli_backend_solid_layout.py` | no direct `agent.services.*` imports outside `context.py` |
| `test_cli_backend_public_api.py` | final public API and deleted legacy namespace |
| `test_cli_backends_workspace_mutation_split.py` | workspace-mutation split contracts |
| `test_cli_backend_detector_script.py` | detector existence, runtime and clean exit |
