"""DI-Adapter-Layer: Factory-Funktionen, die zur Aufrufzeit das aktuelle
Modul-Singleton-Repository liefern.

Eliminiert Cross-File-Test-Order-Kontamination, weil Tests via
``monkeypatch.setattr("agent.services.di.<repo_name>", fake_repo)`` das
Symbol rebinden können, ohne dass ein Service die alte Referenz in
``__init__`` eingefroren hat.

SOLID-Begründung
----------------
- DIP (Dependency Inversion Principle): Services depend on a call-time
  abstraction instead of a module-level import cache. Production code
  resolves the symbol on every call, so a monkeypatched symbol is seen.
- OCP (Open/Closed): New repositories are added by adding a new factory
  function, not by editing service constructors.
- SRP (Single Responsibility): This module owns factory functions only;
  no domain logic, no SQL, no I/O.
- LSP (Liskov Substitution): Any object with the same protocol can stand
  in for the singleton — the factory is substitutable.
- ISP (Interface Segregation): Each factory has a narrow, single-purpose
  contract. No catch-all container.

Late-binding via ``__getattr__``
--------------------------------
Module-level repo symbols (``memory_entry_repo`` etc.) are resolved at
attribute access time from ``agent.repository``. This means:

- ``from agent.services.di import memory_entry_repo`` works (legacy).
- ``monkeypatch.setattr("agent.services.di.memory_entry_repo", fake)``
  works (test-DI). The next attribute read returns the fake.
- ``agent.services.di.memory_entry_repo`` always returns the *current*
  ``agent.repository.memory_entry_repo`` unless monkeypatched.

Backwards compatibility
-----------------------
The legacy module-level singletons in ``agent.repository`` are unchanged.
Existing call sites using ``from agent.repository import X_repo`` keep
working. New code should use the factory functions exposed here.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "get_action_pack_repository",
    "get_agent_repository",
    "get_agent_session_repository",
    "get_archived_task_repository",
    "get_artifact_repository",
    "get_artifact_version_repository",
    "get_audit_repository",
    "get_banned_ip_repository",
    "get_blueprint_artifact_repository",
    "get_blueprint_role_repository",
    "get_blueprint_workflow_step_repository",
    "get_config_repository",
    "get_context_access_policy_repository",
    "get_context_bundle_repository",
    "get_evolution_proposal_repository",
    "get_evolution_run_repository",
    "get_extracted_document_repository",
    "get_goal_repository",
    "get_instruction_overlay_repository",
    "get_knowledge_collection_repository",
    "get_knowledge_index_repository",
    "get_knowledge_index_run_repository",
    "get_knowledge_link_repository",
    "get_login_attempt_repository",
    "get_memory_entry_repository",
    "get_password_history_repository",
    "get_plan_node_repository",
    "get_plan_repository",
    "get_planning_evaluation_repository",
    "get_planning_model_profile_repository",
    "get_planning_pattern_cluster_repository",
    "get_planning_prompt_version_repository",
    "get_planning_review_item_repository",
    "get_planning_run_repository",
    "get_planning_template_candidate_repository",
    "get_playbook_repository",
    "get_policy_decision_repository",
    "get_policy_snapshot_repository",
    "get_refresh_token_repository",
    "get_retrieval_run_repository",
    "get_role_repository",
    "get_scheduled_task_repository",
    "get_stats_repository",
    "get_task_repository",
    "get_team_blueprint_repository",
    "get_team_member_repository",
    "get_team_repository",
    "get_team_type_repository",
    "get_team_type_role_link_repository",
    "get_template_repository",
    "get_terminal_event_repository",
    "get_terminal_session_repository",
    "get_tool_call_repository",
    "get_user_instruction_profile_repository",
    "get_user_repository",
    "get_verification_record_repository",
    "get_worker_job_repository",
    "get_worker_result_repository",
    "get_worker_slot_lease_repository",
]


_KNOWN_REPOS = frozenset({
    "action_pack_repo",
    "agent_repo",
    "agent_session_repo",
    "archived_task_repo",
    "artifact_repo",
    "artifact_version_repo",
    "audit_repo",
    "banned_ip_repo",
    "blueprint_artifact_repo",
    "blueprint_role_repo",
    "blueprint_workflow_step_repo",
    "config_repo",
    "context_access_policy_repo",
    "context_bundle_repo",
    "evolution_proposal_repo",
    "evolution_run_repo",
    "extracted_document_repo",
    "goal_repo",
    "instruction_overlay_repo",
    "knowledge_collection_repo",
    "knowledge_index_repo",
    "knowledge_index_run_repo",
    "knowledge_link_repo",
    "login_attempt_repo",
    "memory_entry_repo",
    "password_history_repo",
    "plan_node_repo",
    "plan_repo",
    "planning_evaluation_repo",
    "planning_model_profile_repo",
    "planning_pattern_cluster_repo",
    "planning_prompt_version_repo",
    "planning_review_item_repo",
    "planning_run_repo",
    "planning_template_candidate_repo",
    "playbook_repo",
    "policy_decision_repo",
    "policy_snapshot_repo",
    "refresh_token_repo",
    "retrieval_run_repo",
    "role_repo",
    "scheduled_task_repo",
    "stats_repo",
    "task_repo",
    "team_blueprint_repo",
    "team_member_repo",
    "team_repo",
    "team_type_repo",
    "team_type_role_link_repo",
    "template_repo",
    "terminal_event_repo",
    "terminal_session_repo",
    "tool_call_repo",
    "user_instruction_profile_repo",
    "user_repo",
    "verification_record_repo",
    "worker_job_repo",
    "worker_result_repo",
    "worker_slot_lease_repo",
})


def _resolve(name: str) -> Any:
    """Look up ``name`` in this module first (test patches), then fall
    back to ``agent.repository``. This is the single source of truth for
    late-binding resolution.
    """
    module_value = globals().get(name, _SENTINEL)
    if module_value is not _SENTINEL:
        return module_value
    from agent import repository
    return getattr(repository, name)


_SENTINEL = object()


def __getattr__(name: str) -> Any:
    """Resolve ``agent.services.di.<name>`` via ``_resolve``."""
    if name in _KNOWN_REPOS:
        return _resolve(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + sorted(_KNOWN_REPOS))


def get_action_pack_repository() -> Any:
    """Call-time lookup for the ``action_pack_repo`` singleton.

    Resolves ``agent.services.di.action_pack_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.action_pack_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.action_pack_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("action_pack_repo")


def get_agent_repository() -> Any:
    """Call-time lookup for the ``agent_repo`` singleton.

    Resolves ``agent.services.di.agent_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.agent_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.agent_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("agent_repo")


def get_agent_session_repository() -> Any:
    """Call-time lookup for the ``agent_session_repo`` singleton.

    Resolves ``agent.services.di.agent_session_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.agent_session_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.agent_session_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("agent_session_repo")


def get_archived_task_repository() -> Any:
    """Call-time lookup for the ``archived_task_repo`` singleton.

    Resolves ``agent.services.di.archived_task_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.archived_task_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.archived_task_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("archived_task_repo")


def get_artifact_repository() -> Any:
    """Call-time lookup for the ``artifact_repo`` singleton.

    Resolves ``agent.services.di.artifact_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.artifact_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.artifact_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("artifact_repo")


def get_artifact_version_repository() -> Any:
    """Call-time lookup for the ``artifact_version_repo`` singleton.

    Resolves ``agent.services.di.artifact_version_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.artifact_version_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.artifact_version_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("artifact_version_repo")


def get_audit_repository() -> Any:
    """Call-time lookup for the ``audit_repo`` singleton.

    Resolves ``agent.services.di.audit_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.audit_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.audit_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("audit_repo")


def get_banned_ip_repository() -> Any:
    """Call-time lookup for the ``banned_ip_repo`` singleton.

    Resolves ``agent.services.di.banned_ip_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.banned_ip_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.banned_ip_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("banned_ip_repo")


def get_blueprint_artifact_repository() -> Any:
    """Call-time lookup for the ``blueprint_artifact_repo`` singleton.

    Resolves ``agent.services.di.blueprint_artifact_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.blueprint_artifact_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.blueprint_artifact_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("blueprint_artifact_repo")


def get_blueprint_role_repository() -> Any:
    """Call-time lookup for the ``blueprint_role_repo`` singleton.

    Resolves ``agent.services.di.blueprint_role_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.blueprint_role_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.blueprint_role_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("blueprint_role_repo")


def get_blueprint_workflow_step_repository() -> Any:
    """Call-time lookup for the ``blueprint_workflow_step_repo`` singleton.

    Resolves ``agent.services.di.blueprint_workflow_step_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.blueprint_workflow_step_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.blueprint_workflow_step_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("blueprint_workflow_step_repo")


def get_config_repository() -> Any:
    """Call-time lookup for the ``config_repo`` singleton.

    Resolves ``agent.services.di.config_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.config_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.config_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("config_repo")


def get_context_access_policy_repository() -> Any:
    """Call-time lookup for the ``context_access_policy_repo`` singleton.

    Resolves ``agent.services.di.context_access_policy_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.context_access_policy_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.context_access_policy_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("context_access_policy_repo")


def get_context_bundle_repository() -> Any:
    """Call-time lookup for the ``context_bundle_repo`` singleton.

    Resolves ``agent.services.di.context_bundle_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.context_bundle_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.context_bundle_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("context_bundle_repo")


def get_evolution_proposal_repository() -> Any:
    """Call-time lookup for the ``evolution_proposal_repo`` singleton.

    Resolves ``agent.services.di.evolution_proposal_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.evolution_proposal_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.evolution_proposal_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("evolution_proposal_repo")


def get_evolution_run_repository() -> Any:
    """Call-time lookup for the ``evolution_run_repo`` singleton.

    Resolves ``agent.services.di.evolution_run_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.evolution_run_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.evolution_run_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("evolution_run_repo")


def get_extracted_document_repository() -> Any:
    """Call-time lookup for the ``extracted_document_repo`` singleton.

    Resolves ``agent.services.di.extracted_document_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.extracted_document_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.extracted_document_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("extracted_document_repo")


def get_goal_repository() -> Any:
    """Call-time lookup for the ``goal_repo`` singleton.

    Resolves ``agent.services.di.goal_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.goal_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.goal_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("goal_repo")


def get_instruction_overlay_repository() -> Any:
    """Call-time lookup for the ``instruction_overlay_repo`` singleton.

    Resolves ``agent.services.di.instruction_overlay_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.instruction_overlay_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.instruction_overlay_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("instruction_overlay_repo")


def get_knowledge_collection_repository() -> Any:
    """Call-time lookup for the ``knowledge_collection_repo`` singleton.

    Resolves ``agent.services.di.knowledge_collection_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.knowledge_collection_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.knowledge_collection_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("knowledge_collection_repo")


def get_knowledge_index_repository() -> Any:
    """Call-time lookup for the ``knowledge_index_repo`` singleton.

    Resolves ``agent.services.di.knowledge_index_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.knowledge_index_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.knowledge_index_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("knowledge_index_repo")


def get_knowledge_index_run_repository() -> Any:
    """Call-time lookup for the ``knowledge_index_run_repo`` singleton.

    Resolves ``agent.services.di.knowledge_index_run_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.knowledge_index_run_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.knowledge_index_run_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("knowledge_index_run_repo")


def get_knowledge_link_repository() -> Any:
    """Call-time lookup for the ``knowledge_link_repo`` singleton.

    Resolves ``agent.services.di.knowledge_link_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.knowledge_link_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.knowledge_link_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("knowledge_link_repo")


def get_login_attempt_repository() -> Any:
    """Call-time lookup for the ``login_attempt_repo`` singleton.

    Resolves ``agent.services.di.login_attempt_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.login_attempt_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.login_attempt_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("login_attempt_repo")


def get_memory_entry_repository() -> Any:
    """Call-time lookup for the ``memory_entry_repo`` singleton.

    Resolves ``agent.services.di.memory_entry_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.memory_entry_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.memory_entry_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("memory_entry_repo")


def get_password_history_repository() -> Any:
    """Call-time lookup for the ``password_history_repo`` singleton.

    Resolves ``agent.services.di.password_history_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.password_history_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.password_history_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("password_history_repo")


def get_plan_node_repository() -> Any:
    """Call-time lookup for the ``plan_node_repo`` singleton.

    Resolves ``agent.services.di.plan_node_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.plan_node_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.plan_node_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("plan_node_repo")


def get_plan_repository() -> Any:
    """Call-time lookup for the ``plan_repo`` singleton.

    Resolves ``agent.services.di.plan_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.plan_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.plan_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("plan_repo")


def get_planning_evaluation_repository() -> Any:
    """Call-time lookup for the ``planning_evaluation_repo`` singleton.

    Resolves ``agent.services.di.planning_evaluation_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_evaluation_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_evaluation_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_evaluation_repo")


def get_planning_model_profile_repository() -> Any:
    """Call-time lookup for the ``planning_model_profile_repo`` singleton.

    Resolves ``agent.services.di.planning_model_profile_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_model_profile_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_model_profile_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_model_profile_repo")


def get_planning_pattern_cluster_repository() -> Any:
    """Call-time lookup for the ``planning_pattern_cluster_repo`` singleton.

    Resolves ``agent.services.di.planning_pattern_cluster_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_pattern_cluster_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_pattern_cluster_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_pattern_cluster_repo")


def get_planning_prompt_version_repository() -> Any:
    """Call-time lookup for the ``planning_prompt_version_repo`` singleton.

    Resolves ``agent.services.di.planning_prompt_version_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_prompt_version_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_prompt_version_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_prompt_version_repo")


def get_planning_review_item_repository() -> Any:
    """Call-time lookup for the ``planning_review_item_repo`` singleton.

    Resolves ``agent.services.di.planning_review_item_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_review_item_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_review_item_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_review_item_repo")


def get_planning_run_repository() -> Any:
    """Call-time lookup for the ``planning_run_repo`` singleton.

    Resolves ``agent.services.di.planning_run_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_run_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_run_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_run_repo")


def get_planning_template_candidate_repository() -> Any:
    """Call-time lookup for the ``planning_template_candidate_repo`` singleton.

    Resolves ``agent.services.di.planning_template_candidate_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.planning_template_candidate_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.planning_template_candidate_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("planning_template_candidate_repo")


def get_playbook_repository() -> Any:
    """Call-time lookup for the ``playbook_repo`` singleton.

    Resolves ``agent.services.di.playbook_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.playbook_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.playbook_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("playbook_repo")


def get_policy_decision_repository() -> Any:
    """Call-time lookup for the ``policy_decision_repo`` singleton.

    Resolves ``agent.services.di.policy_decision_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.policy_decision_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.policy_decision_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("policy_decision_repo")


def get_policy_snapshot_repository() -> Any:
    """Call-time lookup for the ``policy_snapshot_repo`` singleton.

    Resolves ``agent.services.di.policy_snapshot_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.policy_snapshot_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.policy_snapshot_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("policy_snapshot_repo")


def get_refresh_token_repository() -> Any:
    """Call-time lookup for the ``refresh_token_repo`` singleton.

    Resolves ``agent.services.di.refresh_token_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.refresh_token_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.refresh_token_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("refresh_token_repo")


def get_retrieval_run_repository() -> Any:
    """Call-time lookup for the ``retrieval_run_repo`` singleton.

    Resolves ``agent.services.di.retrieval_run_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.retrieval_run_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.retrieval_run_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("retrieval_run_repo")


def get_role_repository() -> Any:
    """Call-time lookup for the ``role_repo`` singleton.

    Resolves ``agent.services.di.role_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.role_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.role_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("role_repo")


def get_scheduled_task_repository() -> Any:
    """Call-time lookup for the ``scheduled_task_repo`` singleton.

    Resolves ``agent.services.di.scheduled_task_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.scheduled_task_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.scheduled_task_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("scheduled_task_repo")


def get_stats_repository() -> Any:
    """Call-time lookup for the ``stats_repo`` singleton.

    Resolves ``agent.services.di.stats_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.stats_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.stats_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("stats_repo")


def get_task_repository() -> Any:
    """Call-time lookup for the ``task_repo`` singleton.

    Resolves ``agent.services.di.task_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.task_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.task_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("task_repo")


def get_team_blueprint_repository() -> Any:
    """Call-time lookup for the ``team_blueprint_repo`` singleton.

    Resolves ``agent.services.di.team_blueprint_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.team_blueprint_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.team_blueprint_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("team_blueprint_repo")


def get_team_member_repository() -> Any:
    """Call-time lookup for the ``team_member_repo`` singleton.

    Resolves ``agent.services.di.team_member_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.team_member_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.team_member_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("team_member_repo")


def get_team_repository() -> Any:
    """Call-time lookup for the ``team_repo`` singleton.

    Resolves ``agent.services.di.team_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.team_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.team_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("team_repo")


def get_team_type_repository() -> Any:
    """Call-time lookup for the ``team_type_repo`` singleton.

    Resolves ``agent.services.di.team_type_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.team_type_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.team_type_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("team_type_repo")


def get_team_type_role_link_repository() -> Any:
    """Call-time lookup for the ``team_type_role_link_repo`` singleton.

    Resolves ``agent.services.di.team_type_role_link_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.team_type_role_link_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.team_type_role_link_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("team_type_role_link_repo")


def get_template_repository() -> Any:
    """Call-time lookup for the ``template_repo`` singleton.

    Resolves ``agent.services.di.template_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.template_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.template_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("template_repo")


def get_terminal_event_repository() -> Any:
    """Call-time lookup for the ``terminal_event_repo`` singleton.

    Resolves ``agent.services.di.terminal_event_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.terminal_event_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.terminal_event_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("terminal_event_repo")


def get_terminal_session_repository() -> Any:
    """Call-time lookup for the ``terminal_session_repo`` singleton.

    Resolves ``agent.services.di.terminal_session_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.terminal_session_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.terminal_session_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("terminal_session_repo")


def get_tool_call_repository() -> Any:
    """Call-time lookup for the ``tool_call_repo`` singleton.

    Resolves ``agent.services.di.tool_call_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.tool_call_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.tool_call_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("tool_call_repo")


def get_user_instruction_profile_repository() -> Any:
    """Call-time lookup for the ``user_instruction_profile_repo`` singleton.

    Resolves ``agent.services.di.user_instruction_profile_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.user_instruction_profile_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.user_instruction_profile_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("user_instruction_profile_repo")


def get_user_repository() -> Any:
    """Call-time lookup for the ``user_repo`` singleton.

    Resolves ``agent.services.di.user_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.user_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.user_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("user_repo")


def get_verification_record_repository() -> Any:
    """Call-time lookup for the ``verification_record_repo`` singleton.

    Resolves ``agent.services.di.verification_record_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.verification_record_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.verification_record_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("verification_record_repo")


def get_worker_job_repository() -> Any:
    """Call-time lookup for the ``worker_job_repo`` singleton.

    Resolves ``agent.services.di.worker_job_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.worker_job_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.worker_job_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("worker_job_repo")


def get_worker_result_repository() -> Any:
    """Call-time lookup for the ``worker_result_repo`` singleton.

    Resolves ``agent.services.di.worker_result_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.worker_result_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.worker_result_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("worker_result_repo")


def get_worker_slot_lease_repository() -> Any:
    """Call-time lookup for the ``worker_slot_lease_repo`` singleton.

    Resolves ``agent.services.di.worker_slot_lease_repo`` via ``_resolve`` (which reads
    a monkeypatched attribute first, then falls back to
    ``agent.repository.worker_slot_lease_repo``) on every call so that tests can
    ``monkeypatch.setattr("agent.services.di.worker_slot_lease_repo", fake_repo)`` and the
    next call returns the fake. Returns the canonical singleton in
    production.
    """
    return _resolve("worker_slot_lease_repo")

