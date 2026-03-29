from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, current_app

from agent.services.goal_service import GoalService, get_goal_service
from agent.services.lifecycle_service import GoalLifecycleService, get_goal_lifecycle_service
from agent.services.planning_service import PlanningService, get_planning_service
from agent.services.result_memory_service import ResultMemoryService, get_result_memory_service
from agent.services.task_queue_service import TaskQueueService, get_task_queue_service
from agent.services.verification_service import VerificationService, get_verification_service
from agent.services.worker_job_service import WorkerJobService, get_worker_job_service


@dataclass(frozen=True)
class CoreServiceRegistry:
    goal_service: GoalService
    goal_lifecycle_service: GoalLifecycleService
    planning_service: PlanningService
    task_queue_service: TaskQueueService
    verification_service: VerificationService
    worker_job_service: WorkerJobService
    result_memory_service: ResultMemoryService


def build_core_service_registry() -> CoreServiceRegistry:
    return CoreServiceRegistry(
        goal_service=get_goal_service(),
        goal_lifecycle_service=get_goal_lifecycle_service(),
        planning_service=get_planning_service(),
        task_queue_service=get_task_queue_service(),
        verification_service=get_verification_service(),
        worker_job_service=get_worker_job_service(),
        result_memory_service=get_result_memory_service(),
    )


def initialize_core_services(app: Flask) -> CoreServiceRegistry:
    registry = build_core_service_registry()
    app.extensions["core_services"] = registry
    return registry


def get_core_services(app: Flask | None = None) -> CoreServiceRegistry:
    target_app = app or current_app
    registry = target_app.extensions.get("core_services")
    if registry is None:
        registry = initialize_core_services(target_app)
    return registry
