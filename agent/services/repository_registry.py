from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, current_app

from agent.repository import (
    agent_repo,
    artifact_repo,
    artifact_version_repo,
    archived_task_repo,
    config_repo,
    extracted_document_repo,
    goal_repo,
    knowledge_collection_repo,
    knowledge_index_repo,
    knowledge_index_run_repo,
    knowledge_link_repo,
    memory_entry_repo,
    plan_node_repo,
    plan_repo,
    policy_decision_repo,
    role_repo,
    stats_repo,
    task_repo,
    team_repo,
    template_repo,
    verification_record_repo,
)


@dataclass(frozen=True)
class RepositoryRegistry:
    agent_repo: object
    artifact_repo: object
    artifact_version_repo: object
    archived_task_repo: object
    config_repo: object
    extracted_document_repo: object
    goal_repo: object
    knowledge_collection_repo: object
    knowledge_index_repo: object
    knowledge_index_run_repo: object
    knowledge_link_repo: object
    memory_entry_repo: object
    plan_node_repo: object
    plan_repo: object
    policy_decision_repo: object
    role_repo: object
    stats_repo: object
    task_repo: object
    team_repo: object
    template_repo: object
    verification_record_repo: object


def build_repository_registry() -> RepositoryRegistry:
    return RepositoryRegistry(
        agent_repo=agent_repo,
        artifact_repo=artifact_repo,
        artifact_version_repo=artifact_version_repo,
        archived_task_repo=archived_task_repo,
        config_repo=config_repo,
        extracted_document_repo=extracted_document_repo,
        goal_repo=goal_repo,
        knowledge_collection_repo=knowledge_collection_repo,
        knowledge_index_repo=knowledge_index_repo,
        knowledge_index_run_repo=knowledge_index_run_repo,
        knowledge_link_repo=knowledge_link_repo,
        memory_entry_repo=memory_entry_repo,
        plan_node_repo=plan_node_repo,
        plan_repo=plan_repo,
        policy_decision_repo=policy_decision_repo,
        role_repo=role_repo,
        stats_repo=stats_repo,
        task_repo=task_repo,
        team_repo=team_repo,
        template_repo=template_repo,
        verification_record_repo=verification_record_repo,
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
