# Native Worker Setup

## Requirements

- Python 3.11+
- No cloud API key required for local/native mode
- SQLite or PostgreSQL for memory persistence

## Running the worker

```bash
# Standalone CLI (single command)
python -m worker.cli.standalone_worker_cli --command "ls ." --workspace /tmp/workspace

# Via agent service (native runtime enabled)
# Set in config: worker_runtime.native_worker_runtime.enabled = true
```

## Test commands

```bash
# All AWF tests (T001–T045)
python -m pytest tests/test_awf_worker_fixup_t001_t010.py \
                 tests/test_awf_worker_fixup_t011_t020.py \
                 tests/test_awf_worker_fixup_t021_t030.py \
                 tests/test_awf_worker_fixup_t031_t045.py -q

# Security regression only
python -m pytest tests/test_worker_security_regression.py -q

# Core worker modules
python -m pytest tests/ -k "worker" -q
```

## Enforcement gates (active by default)

| Gate | Module | Default |
|---|---|---|
| PreflightGate | `worker/core/preflight.py` | fail-closed |
| CapabilityGrant | `worker/core/execution_envelope.py` | SHA256 snapshot hash |
| WorkerToolRegistry | `worker/core/tool_registry.py` | tool must be registered |
| ResourceLimitEnforcer | `worker/core/tool_registry.py` | timeout + output cap |
| ProviderSelectionGate | `worker/core/provider_registry.py` | Hub-driven selection |
| ContextBudgetGate | `worker/core/context_resolver.py` | token budget with reserve |
| ContextSensitivityFilter | `worker/core/context_resolver.py` | confidential/secret blocked for cloud |
| MemoryPolicy | `agent/services/result_memory_service.py` | redact before persist |
| SkillRegistry | `worker/skills/skill_registry.py` | disabled by default |
| SubworkerEnvelope | `worker/core/subworker_envelope.py` | subset enforcement |
| AuditEvents | `worker/core/audit_events.py` | fail-closed on unknown event |

## Skills

Baseline skills are read-only and proposal-only. Load them:

```python
from worker.skills.skill_registry import SkillRegistry
from worker.skills.builtin.manifests import load_builtin_skills

reg = SkillRegistry()
load_builtin_skills(reg)
reg.enable("repo_context_review")  # explicit enable required
```

## Memory policy

```python
policy = {
    "enabled": True,
    "redact_before_persist": True,       # default: True
    "archive_raw_output": False,          # default: False (safe)
    "default_memory_scope": "task",
    "default_ttl_seconds": None,          # None = keep forever
    "sensitivity": "internal",
}
```

## Diagnostics

```python
from worker.core.diagnostics import build_worker_diagnostics_read_model
diag = build_worker_diagnostics_read_model(
    native_worker_enabled=True,
    tool_registry=my_tool_registry,
    skill_registry=my_skill_registry,
)
print(diag.as_dict())
assert not diag.has_secrets()
```
