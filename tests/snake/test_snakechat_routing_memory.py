from __future__ import annotations

from client_surfaces.operator_tui.snake_chat_command_router import SnakeChatCommandRouter
from client_surfaces.operator_tui.snake_chat_routing_memory import SnakeChatRoutingMemory
from client_surfaces.operator_tui.snake_chat_security_policy import SnakeChatSecurityPolicy


def test_routing_memory_learns_safe_read_pattern_without_llm() -> None:
    memory = SnakeChatRoutingMemory()
    router = SnakeChatCommandRouter(
        routing_memory=memory,
        enable_llm_classifier=True,
    )
    learned = router.learn_safe_pattern(
        question="gib mir alle dateien im hauptverzeichnis",
        route="filesystem_read",
        tool_args={"path_hint": "."},
    )

    decision = router.route("Gib mir alle Dateien im Hauptverzeichnis?")

    assert learned is True
    assert decision.route == "filesystem_read"
    assert decision.method == "memory_pattern"
    assert decision.route_source == "memory_pattern"
    assert decision.tool_args == {"path_hint": "."}


def test_routing_memory_rejects_write_or_execute_routes() -> None:
    memory = SnakeChatRoutingMemory()
    router = SnakeChatCommandRouter(routing_memory=memory)

    learned = router.learn_safe_pattern(
        question="loesche alles",
        route="delete_file",
    )

    assert learned is False
    assert memory.entries() == []


def test_router_direct_tool_telemetry_for_keyword_match() -> None:
    router = SnakeChatCommandRouter()

    decision = router.route("zeige git status")

    assert decision.route == "git_read"
    assert decision.route_source == "direct_tool"
    assert decision.output_mode == "structured"
    assert decision.policy_reason == "allowed"
    assert decision.tool_args == {"git_subcommand": "status"}


def test_router_policy_block_records_telemetry() -> None:
    policy = SnakeChatSecurityPolicy(allow_git_read=False)
    router = SnakeChatCommandRouter(policy=policy)

    decision = router.route("zeige git status")

    assert decision.route == "llm_answer"
    assert decision.blocked is True
    assert decision.route_source == "blocked_by_policy"
    assert decision.policy_reason == "git_read_disabled"
