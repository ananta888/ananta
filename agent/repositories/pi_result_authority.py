"""SQL adapter: lock current Pi authority until the enclosing Task commit."""

import math

import sqlalchemy as sa

from agent.db_models.agents import AgentInfoDB
from agent.db_models.workflow_runtime import (
    WorkflowAuthorizationGrantDB,
    WorkflowExecutionOwnershipDB,
    WorkflowWorkerAssignmentDB,
)
from agent.ports.pi_result_authority import PiResultAuthority
from agent.services.workflow_authorization_grant_service import require_current_workflow_grant_row
from agent.services.workflow_runtime.sqlalchemy_ownership import execution_ownership_from_row
from agent.services.workflow_runtime.sqlalchemy_support import stable_row_id
from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE


class SQLAlchemyPiResultAuthority:
    def __init__(self, *, key_ring):
        self._key_ring = key_ring

    def require_current(self, *, session, command, task, now):
        if not math.isfinite(now):
            raise ValueError("pi_native_result_clock_invalid")
        ownership_row = _locked_row(session, WorkflowExecutionOwnershipDB, stable_row_id(
            "wfro", command.tenant_id, command.run_id, command.node.node_id,
        ))
        ownership = execution_ownership_from_row(ownership_row)
        expected = {
            "tenant_id": command.tenant_id, "workflow_id": command.workflow_id,
            "run_id": command.run_id, "step_id": command.node.node_id,
            "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
        }
        if (
            any(getattr(ownership, field) != value for field, value in expected.items())
            or ownership.owner_id != f"hub-native:{command.run_id}:{command.node.node_id}"
            or ownership.status != "active" or ownership.lease_expires_at <= now
        ):
            raise ValueError("pi_native_result_lease_not_current")
        assignment = _locked_row(session, WorkflowWorkerAssignmentDB, stable_row_id(
            "wfra", command.tenant_id, command.run_id, command.node.node_id,
        ))
        if (
            any(getattr(assignment, field) != value for field, value in expected.items())
            or assignment.hub_task_id != task.id or assignment.worker_url != task.assigned_agent_url
            or type(assignment.revision) is not int or assignment.revision < 1
            or not math.isfinite(assignment.assigned_at) or not 0 < assignment.assigned_at <= now
        ):
            raise ValueError("pi_native_result_assignment_not_current")
        worker = _locked_row(session, AgentInfoDB, assignment.worker_url)
        capabilities = worker.authorized_capabilities
        required = {"workflow.adapter.native", "coding.agent.pi", *command.node.required_capabilities}
        if (
            worker.name != assignment.worker_id or worker.role != "worker"
            or worker.registration_validated is not True
            or worker.registration_provenance != STRICT_WORKER_REGISTRATION_PROVENANCE
            or not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities)
            or not required.issubset(capabilities)
        ):
            raise ValueError("pi_native_result_worker_not_authorized")
        envelope = command.authorization
        envelope.verify(
            key_ring=self._key_ring(), tenant_id=command.tenant_id, workflow_id=command.workflow_id,
            run_id=command.run_id, step_id=command.node.node_id,
            plan_hash=command.plan_hash, policy_version=command.policy_version, now=now,
        )
        grant = require_current_workflow_grant_row(
            _locked_row(session, WorkflowAuthorizationGrantDB, envelope.envelope_id), envelope, now=now,
        )
        return PiResultAuthority(
            assignment.id, assignment.revision, worker.name, worker.url, ownership.revision, grant.revision,
        )


def _locked_row(session, model, identity):
    key = sa.inspect(model).primary_key[0]
    statement = sa.select(model).where(key == identity)
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update()
    row = session.execute(statement.execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        raise ValueError("pi_native_result_authority_missing")
    return row
