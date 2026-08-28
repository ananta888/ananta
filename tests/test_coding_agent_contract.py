from pathlib import Path

import pytest

from agent.cli_backends.coding_agent_contract import (
    CodingAgentCapabilities,
    CodingAgentDescriptor,
    CodingAgentRunRequest,
    FreeClass,
    IntegrationKind,
)
from agent.cli_backends.coding_agent_profiles import coding_agent_descriptors


def test_descriptor_uses_closed_integration_and_cost_taxonomies() -> None:
    descriptor = CodingAgentDescriptor(
        provider_id="Example_Agent",
        display_name="Example",
        integration_kind=IntegrationKind.CLI,
        free_class=FreeClass.OPEN_SOURCE_BYOK,
        capabilities=CodingAgentCapabilities(headless=True, structured_output=True),
    )

    assert descriptor.provider_id == "example_agent"
    assert descriptor.as_dict()["integration_kind"] == "cli"
    assert descriptor.as_dict()["capabilities"]["headless"] is True


def test_catalog_distinguishes_cloud_ide_and_byok_products() -> None:
    descriptors = {item.provider_id: item for item in coding_agent_descriptors()}

    assert descriptors["qwen_code"].free_class is FreeClass.OPEN_SOURCE_BYOK
    assert descriptors["jules"].integration_kind is IntegrationKind.CLOUD_AGENT
    assert descriptors["windsurf"].integration_kind is IntegrationKind.IDE_EXTERNAL
    assert descriptors["windsurf"].capabilities.headless is False


def test_run_request_rejects_unbounded_or_interactive_contracts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout_invalid"):
        CodingAgentRunRequest(prompt="work", workspace=tmp_path, timeout_seconds=0)
    with pytest.raises(ValueError, match="permission_mode_invalid"):
        CodingAgentRunRequest(
            prompt="work",
            workspace=tmp_path,
            timeout_seconds=10,
            permission_mode="ask_human",
        )


def test_run_request_rejects_workspace_path_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError, match="outside_allowed_root"):
        CodingAgentRunRequest(
            prompt="work",
            workspace=outside,
            workspace_root=allowed,
            timeout_seconds=10,
        )
