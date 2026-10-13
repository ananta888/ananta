"""Explicit Hub composition; retirement shares the cadence admission and policy."""

from agent.repositories.native_maintenance_cadence import SQLMaintenanceCadenceRepository
from agent.repositories.native_result_retirement import SQLNativeResultRetirementRepository
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from agent.services.native_result_maintenance_policy import configured_maintenance_schedules, maintenance_request
from agent.services.native_result_maintenance_runtime import build_native_maintenance_admission
from agent.services.native_result_retirement_service import NativeResultRetirementService
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY


class RetirementIssuancePolicy:
    def __init__(self, *, configuration, admission, reservations):
        self._configuration, self._admission, self._reservations = configuration, admission, reservations

    def __call__(self, task, command):
        for schedule in configured_maintenance_schedules(self._configuration()):
            if (schedule.retire_expired and schedule.tenant_id == task.tenant_id
                    and schedule.project_id == task.project_id and schedule.control_task_id == command.control_task_id
                    and maintenance_request(schedule, command.run_id).plan.plan_hash == command.plan_hash):
                self._admission.require(schedule)
                self._reservations.require_current(schedule, run_id=command.run_id)
                return
        raise ValueError('native_result_retirement_current_policy_required')


def get_native_result_retirement_service(app):
    if str(app.config.get('ROLE') or '').lower() != 'hub':
        raise ValueError('native_result_retirement_hub_only')
    service = app.extensions.get('native_result_retirement_service')
    if service is None:
        from agent.database import engine
        from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring

        service = NativeResultRetirementService(repository=SQLNativeResultRetirementRepository(engine),
            authority=SQLAlchemyNativeResultAuthority(key_ring=get_workflow_authorization_key_ring,
                required_capabilities=(RETIREMENT_CAPABILITY,)), keys=get_workflow_authorization_key_ring,
            policy=RetirementIssuancePolicy(
                configuration=lambda: (app.config.get('AGENT_CONFIG') or {}).get('native_result_maintenance'),
                admission=build_native_maintenance_admission(app, engine=engine),
                reservations=SQLMaintenanceCadenceRepository(engine)))
        app.extensions['native_result_retirement_service'] = service
    return service
