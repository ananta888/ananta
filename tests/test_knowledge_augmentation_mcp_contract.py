from agent.services.knowledge_augmentation_adapters import KnowledgeAugmentationAdapters
from agent.services.knowledge_augmentation_policy_service import KnowledgeAugmentationPolicyService
from agent.services.mcp_registry_service import MCPRegistryService


def test_mcp_augmentation_tool_uses_canonical_default_off_policy():
    adapters = KnowledgeAugmentationAdapters(
        KnowledgeAugmentationPolicyService(profiles={"default": {"mode": "auto"}}, policy_digest="a" * 64)
    )
    context = {
        "knowledge_augmentation_adapters": adapters,
        "knowledge_augmentation_context": {
            "global_enabled": False,
            "model_enabled": True,
            "task_enabled": True,
            "domain_enabled": True,
            "data_class_enabled": True,
            "runtime_ready": True,
            "expert_selected": True,
            "citation_required": False,
            "rag_available": True,
        },
    }
    result = MCPRegistryService().call_tool(
        name="knowledge_augmentation.decide",
        arguments={"profile_id": "default"},
        context=context,
    )
    decision = result["content"][0]["json"]
    assert decision["mode"] == "rag_only"
    assert decision["use_expert"] is False
    tool = next(item for item in MCPRegistryService().list_tools() if item["name"] == "knowledge_augmentation.decide")
    assert "expert_id" not in tool["inputSchema"]["properties"]
