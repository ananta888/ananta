"""Installed execution composition from a previously verified signed Hub grant."""

import os

from ananta_contracts.meet_companion_task_grant import validate_model_execution
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_cuda_cache import CachedCompanionExecution, TaskCudaCache
from worker.meet_media.companion_task_environment import assigned_model_environment
from worker.meet_media.companion_task_model_process import AssignedCompanionModelProcess
from worker.meet_media.companion_task_tool_process import AssignedCompanionToolProcess


def assigned_execution(client, grant):
    environment = assigned_model_environment(client.assignment)
    profile = validate_model_execution(grant["model_execution"]) if "model_execution" in grant else None
    cache = TaskCudaCache(parent=os.environ.get("ANANTA_MEET_TASK_SCRATCH_DIR", "/tmp"))
    try:
        environment.update(cache.environment)
        options = {"environment": environment}
        if profile is not None:
            options.update(model_profile=profile,
                ports={"tool": AssignedCompanionToolProcess(client), "model": AssignedCompanionModelProcess(client)})
        execution = CompanionExecutionProcess(**options)
        return CachedCompanionExecution(execution, cache)
    except BaseException:
        cache.close()
        raise
