from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, current_app

from agent.repository import (
    action_pack_repo,
    agent_repo,
    audit_repo,
    artifact_repo,
    artifact_version_repo,
    archived_task_repo,
    banned_ip_repo,
    blueprint_artifact_repo,
    blueprint_role_repo,
    config_repo,
    context_bundle_repo,
    extracted_document_repo,
    evolution_proposal_repo,
    evolution_run_repo,
    goal_repo,
    knowledge_collection_repo,
    knowledge_index_repo,
    knowledge_index_run_repo,
    knowledge_link_repo,
    login_attempt_repo,
    memory_entry_repo,
    password_history_repo,
    plan_node_repo,
    playbook_repo,
    plan_repo,
    policy_decision_repo,
    refresh_token_repo,
    retrieval_run_repo,
    role_repo,
    scheduled_task_repo,
    stats_repo,
    task_repo,
    team_blueprint_repo,
    team_member_repo,
    team_repo,
    team_type_repo,
    team_type_role_link_repo,
    template_repo,
    user_repo,
    verification_record_repo,
    worker_job_repo,
    worker_result_repo,
)


@dataclass(frozen=True)
class RepositoryRegistry:
    action_pack_repo: object
    agent_repo: object
    audit_repo: object
    artifact_repo: object
    artifact_version_repo: object
    archived_task_repo: object
    banned_ip_repo: object
    blueprint_artifact_repo: object
    blueprint_role_repo: object
    config_repo: object
    context_bundle_repo: object
    extracted_document_repo: object
    evolution_proposal_repo: object
    evolution_run_repo: object
    goal_repo: object
    knowledge_collection_repo: object
    knowledge_index_repo: object
    knowledge_index_run_repo: object
    knowledge_link_repo: object
    login_attempt_repo: object
    memory_entry_repo: object
    password_history_repo: object
    plan_node_repo: object
    playbook_repo: object
    plan_repo: object
    policy_decision_repo: object
    refresh_token_repo: object
    retrieval_run_repo: object
    role_repo: object
    scheduled_task_repo: object
    stats_repo: object
    task_repo: object
    team_blueprint_repo: object
    team_member_repo: object
    team_repo: object
    team_type_repo: object
    team_type_role_link_repo: object
    template_repo: object
    user_repo: object
    verification_record_repo: object
    worker_job_repo: object
    worker_result_repo: object


def build_repository_registry() -> RepositoryRegistry:
    return RepositoryRegistry(
        action_pack_repo=action_pack_repo,
        agent_repo=agent_repo,
        audit_repo=audit_repo,
        artifact_repo=artifact_repo,
        artifact_version_repo=artifact_version_repo,
        archived_task_repo=archived_task_repo,
        banned_ip_repo=banned_ip_repo,
        blueprint_artifact_repo=blueprint_artifact_repo,
        blueprint_role_repo=blueprint_role_repo,
        config_repo=config_repo,
        context_bundle_repo=context_bundle_repo,
        extracted_document_repo=extracted_document_repo,
        evolution_proposal_repo=evolution_proposal_repo,
        evolution_run_repo=evolution_run_repo,
        goal_repo=goal_repo,
        knowledge_collection_repo=knowledge_collection_repo,
        knowledge_index_repo=knowledge_index_repo,
        knowledge_index_run_repo=knowledge_index_run_repo,
        knowledge_link_repo=knowledge_link_repo,
        login_attempt_repo=login_attempt_repo,
        memory_entry_repo=memory_entry_repo,
        password_history_repo=password_history_repo,
        plan_node_repo=plan_node_repo,
        playbook_repo=playbook_repo,
        plan_repo=plan_repo,
        policy_decision_repo=policy_decision_repo,
        refresh_token_repo=refresh_token_repo,
        retrieval_run_repo=retrieval_run_repo,
        role_repo=role_repo,
        scheduled_task_repo=scheduled_task_repo,
        stats_repo=stats_repo,
        task_repo=task_repo,
        team_blueprint_repo=team_blueprint_repo,
        team_member_repo=team_member_repo,
        team_repo=team_repo,
        team_type_repo=team_type_repo,
        team_type_role_link_repo=team_type_role_link_repo,
        template_repo=template_repo,
        user_repo=user_repo,
        verification_record_repo=verification_record_repo,
        worker_job_repo=worker_job_repo,
        worker_result_repo=worker_result_repo,
    )


def initialize_repository_registry(app: Flask) -> RepositoryRegistry:
    registry = build_repository_registry()
    app.extensions["repository_registry"] = registry
    return registry


def get_repository_registry(app: Flask | None = None) -> RepositoryRegistry:
    if app is not None:
        target_app = app
        registry = target_app.extensions.get("repository_registry")
        if registry is None:
            registry = initialize_repository_registry(target_app)
        return registry
    try:
        target_app = current_app
        registry = target_app.extensions.get("repository_registry")
        if registry is None:
            registry = initialize_repository_registry(target_app)
        return registry
    except RuntimeError:
        return build_repository_registry()
