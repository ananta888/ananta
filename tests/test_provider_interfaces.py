from __future__ import annotations

from pathlib import Path

from agent.providers.interfaces import ProviderDescriptor, ProviderHealthReport

ROOT = Path(__file__).resolve().parents[1]
INTERFACES_PATH = ROOT / "agent" / "providers" / "interfaces.py"


def test_provider_descriptor_defaults_to_disabled_and_normalizes_values() -> None:
    descriptor = ProviderDescriptor(
        provider_id=" Worker_Runtime ",
        provider_family="worker_execution",
        capabilities=("Execute", "EXECUTE", "dry_run"),
        risk_class="HIGH",
    )
    assert descriptor.provider_id == "Worker_Runtime"
    assert descriptor.provider_family == "worker_execution"
    assert descriptor.enabled_by_default is False
    assert descriptor.risk_class == "high"
    assert descriptor.capabilities == ("execute", "execute", "dry_run")


def test_provider_interfaces_do_not_depend_on_optional_provider_packages() -> None:
    source = INTERFACES_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("n8n", "blender", "kicad", "freecad", "opencode", "aider"):
        assert forbidden not in source
    health = ProviderHealthReport(status="healthy")
    assert health.status == "healthy"
