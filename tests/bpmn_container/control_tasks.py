"""Create the actual SQL project/control Task for an owned synthetic scenario."""

from sqlmodel import Session

from agent.database import engine
from agent.db_models import TaskDB
from agent.db_models.projects import ProjectDB


def create_control_task(request):
    with Session(engine) as session, session.begin():
        project_id = request.plan.metadata['workflow_rollout_scope']['project_id']
        if session.get(ProjectDB, (request.plan.tenant_id, project_id)) is None:
            session.add(ProjectDB(tenant_id=request.plan.tenant_id, project_id=project_id,
                name='Synthetic full-app reference project', created_by_subject_id='synthetic-hub-policy'))
            session.flush()
        session.add(TaskDB(id=request.control_task_id, tenant_id=request.plan.tenant_id, project_id=project_id,
            status='running', title='Synthetic Hub reference controller', task_kind='bpmn_reference_control'))
