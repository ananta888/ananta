"""Task-scoped execution internals (CLN-057).

The former flat ``agent/services/_task_scoped_*`` helper family now lives in
this package. ``TaskScopedExecutionService`` stays the single public entry
point (propose_task_step / execute_task_step / forwarding_dependencies).
"""
