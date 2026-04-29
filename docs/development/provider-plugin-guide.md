# Provider Plugin Guide

This guide explains where provider code belongs and how to add integrations without breaking Core.

## Layout

| Purpose | Path |
| --- | --- |
| Provider-neutral interfaces | `agent/providers/interfaces.py` + family-specific contracts in `agent/providers/*.py` |
| Provider registry | `agent/providers/registry.py` |
| Provider implementation code | adapter/service modules (for example `worker/adapters`, `agent/services/workflow_providers`) |
| Provider schemas | neutral contracts in `schemas/worker/*` and `schemas/artifacts/*` |
| Provider contract tests | `tests/test_*provider*.py` |

## Mandatory rules

1. Providers are **disabled by default** unless explicitly enabled by runtime/profile.
2. Missing optional dependencies must produce **degraded** status, not core startup failure.
3. Dry-run behavior should exist where feasible, especially for workflow and worker providers.
4. Provider payloads must pass through redaction (`agent/providers/redaction.py`) before logs/artifacts.
5. Provider results should include standard provenance (`agent/providers/provenance.py`).
6. Core modules must not import provider-specific SDK/runtime modules directly.

## Minimal mock provider example

```python
from agent.providers.interfaces import ProviderDescriptor, ProviderHealthReport
from agent.providers.registry import GenericProviderRegistry


class MockWorkflowProvider:
    descriptor = ProviderDescriptor(
        provider_id="mock_workflow",
        provider_family="workflow",
        capabilities=("dry_run",),
        risk_class="low",
        enabled_by_default=False,
    )

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(status="healthy")


registry = GenericProviderRegistry()
registry.register_provider(descriptor=MockWorkflowProvider.descriptor, factory=MockWorkflowProvider)
```

## Governance references

- ADR: `docs/decisions/ADR-core-boundary-and-provider-plugins.md`
- Checklist: `docs/architecture/medusa-risk-checklist.md`
