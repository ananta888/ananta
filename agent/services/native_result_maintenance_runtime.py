"""Hub composition of durable cadence, mandatory admission and Native delegation."""

import time

from agent.repositories.native_maintenance_cadence import SQLMaintenanceCadenceRepository
from agent.repositories.projects import ProjectRepository
from agent.repositories.task_repository_session import task_repository_session
from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.native_control_task_scope import native_control_task_scope
from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
from agent.services.native_graph_task_queue_adapter import build_native_graph_task_queue_adapter
from agent.services.native_result_maintenance_cadence import NativeResultMaintenanceCadence
from agent.services.native_result_maintenance_owner import require_registered_result_store_owner
from agent.services.native_result_maintenance_policy import (
    MaintenanceSchedule,
    configured_maintenance_schedules,
    maintenance_request,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
from agent.services.workflow_control_production_composition import (
    production_command_replay_store,
    production_release_admission,
    production_rollout_policies,
    production_runtime_health,
)
from agent.services.workflow_control_release_selection import PROTECTED_RUNTIME_CAPABILITIES
from agent.services.workflow_runtime.commands import SignedWorkflowCommand, WorkflowCommandVerifier
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore, SQLAlchemyEventStore
from agent.services.workflow_runtime.sqlalchemy_ownership import SQLAlchemyExecutionOwnershipStore
from agent.services.workflow_runtime.sqlalchemy_side_effects import SQLAlchemySideEffectLedger
from agent.services.workflow_runtime_capability_service import default_workflow_runtime_capability_service
from agent.services.workflow_runtime_rollout_service import RolloutAwareRuntimeSelection
from agent.services.workflow_runtime_selection_composition import build_configured_workflow_runtime_selection
from ananta_contracts.native_result_maintenance import MAINTENANCE_TOOL
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY, RETIREMENT_TOOL

_ACTOR = 'hub:native-result-maintenance'


class _NativeBackendDescriptor:
    backend_id = 'local'


class _CurrentReleaseAdmission:
    def evaluate(self, **values):
        return production_release_admission(None).evaluate(**values)


class RegisteredMaintenanceAdmission:
    def __init__(self, *, engine, registry, release, selection=None, governance=lambda: {}, clock=time.time):
        self._engine, self._registry, self._release, self._clock = engine, registry, release, clock
        self._selection = selection
        self._governance = governance

    def require(self, schedule):
        lifecycle = (self._governance() or {}).get('approval_lifecycle') or {}
        required = lifecycle.get('human_required_tools') or []
        if not isinstance(required, list) or any(not isinstance(tool, str) for tool in required):
            raise ValueError('native_maintenance_approval_policy_invalid')
        tools = {MAINTENANCE_TOOL, RETIREMENT_TOOL} if schedule.retire_expired else {MAINTENANCE_TOOL}
        if tools & {tool.strip() for tool in required}:
            raise PermissionError('native_maintenance_mandatory_approval_required')
        registry = self._registry()
        task = registry.task_repo.get_by_id(schedule.control_task_id)
        if task is None:
            raise ValueError('native_maintenance_control_task_required')
        scope = native_control_task_scope(task.model_dump(), command=schedule)
        if scope['project_id'] != schedule.project_id:
            raise ValueError('native_maintenance_project_binding_mismatch')
        with task_repository_session(self._engine) as session:
            project = ProjectRepository(session).get(schedule.tenant_id, schedule.project_id)
            if project is None or project.status != 'active':
                raise ValueError('native_maintenance_project_inactive')
        worker = registry.agent_repo.get_by_url(schedule.owner['worker_url'])
        require_registered_result_store_owner(worker, schedule.owner, now=self._clock())
        if schedule.retire_expired and RETIREMENT_CAPABILITY not in (worker.authorized_capabilities or ()):
            raise ValueError('native_result_retirement_worker_not_authorized')
        plan = maintenance_request(schedule, 'admission-preview').plan
        release = self._release() if callable(self._release) else self._release
        admitted, _ = release.evaluate(plan=plan, runtime_id='ananta-native', runtime_version='1.0.0',
            required_capabilities=frozenset(plan.capabilities) | PROTECTED_RUNTIME_CAPABILITIES)
        if admitted is not True:
            raise ValueError('native_maintenance_runtime_release_denied')
        if self._selection is not None:
            selected = self._selection.select(plan=plan, preferred_runtime='ananta-native',
                allowed_runtimes=('ananta-native',))
            if selected.runtime_id != 'ananta-native' or selected.mode != 'live':
                raise ValueError('native_maintenance_runtime_selection_denied')


class MaintenanceControlPolicy:
    def __init__(self, *, configuration, admission, reservations):
        self._configuration, self._admission = configuration, admission
        self._reservations = reservations

    def authorize_delegation(self, *, plan, node, state):
        try:
            schedule = MaintenanceSchedule.from_mapping(plan.metadata['maintenance_schedule'])
            current = configured_maintenance_schedules(self._configuration())
            if (state.status != 'running' or not any(item.digest == schedule.digest for item in current)
                    or plan.plan_hash != maintenance_request(schedule, 'policy-preview').plan.plan_hash):
                return False, 'native_maintenance_policy_withdrawn'
            self._admission.require(schedule)
            self._reservations.require_current(schedule, run_id=state.control_lease.checkpoint.run_id)
            return True, 'native_maintenance_hub_preauthorized'
        except Exception:
            return False, 'native_maintenance_policy_denied'

    def authorize_command(self, command, *, plan, state):
        allowed = command.command_type == 'cancel' and command.actor_id == _ACTOR
        return allowed, 'native_maintenance_hub_cancel' if allowed else 'native_maintenance_control_denied'


class NativeMaintenanceRuns:
    def __init__(self, *, runtime_factory, checkpoints, keys, retire_unstarted, clock=time.time):
        self._factory, self._checkpoints, self._keys, self._clock = runtime_factory, checkpoints, keys, clock
        self._retire_unstarted = retire_unstarted

    def _checkpoint(self, request):
        return self._checkpoints.get_latest(tenant_id=request.plan.tenant_id,
            run_id=request.run_id, task_id=request.control_task_id)

    def advance(self, reservation):
        request = maintenance_request(reservation.schedule, reservation.run_id)
        runtime = self._factory()
        if self._checkpoint(request) is None:
            return runtime.start(request)
        return runtime.advance(request)

    def cancel(self, reservation):
        request = maintenance_request(reservation.schedule, reservation.run_id)
        runtime = self._factory()
        if self._checkpoint(request) is None:
            # This automation persists its initial checkpoint before any queue
            # effect. The same signed Run lease excludes a concurrent starter.
            leases = BpmnRunLeaseService(checkpoints=self._checkpoints, keys=self._keys(), clock=self._clock)
            with leases.acquire(request) as lease:
                if self._checkpoint(request) is None:
                    self._retire_unstarted(reservation, lease=lease)
                    return 'cancelled'
        current = runtime.inspect(request)
        if current.status in {'completed', 'failed', 'cancelled'}:
            return current.status
        checkpoint = current.checkpoint
        command = SignedWorkflowCommand.issue(key_ring=self._keys(), command_type='cancel',
            tenant_id=request.plan.tenant_id, workflow_id=request.plan.workflow_id, run_id=request.run_id,
            step_id='expire-results', checkpoint_id=checkpoint.checkpoint_id, expected_revision=checkpoint.revision,
            plan_hash=checkpoint.plan_hash, policy_version=checkpoint.policy_version,
            actor_id=_ACTOR, actor_roles=('system',), now=self._clock(), ttl_seconds=60)
        cancelled = runtime.resume(request, command=command)
        if cancelled.status != 'cancelled':
            raise ValueError('native_maintenance_cancel_not_confirmed')
        return 'cancelled'


def build_native_result_maintenance_cadence(app):
    from agent.database import engine
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring

    def configuration():
        return (app.config.get('AGENT_CONFIG') or {}).get('native_result_maintenance')
    checkpoints = SQLAlchemyCheckpointStore(engine)
    reservations = SQLMaintenanceCadenceRepository(engine)
    admission = build_native_maintenance_admission(app, engine=engine)

    def runtime():
        keys = get_workflow_authorization_key_ring()
        return NativeGraphOrchestrator(queue=build_native_graph_task_queue_adapter(), checkpoints=checkpoints,
            events=SQLAlchemyEventStore(engine), ownership=SQLAlchemyExecutionOwnershipStore(engine),
            ledger=SQLAlchemySideEffectLedger(engine), key_ring=keys,
            command_verifier=WorkflowCommandVerifier(keys, production_command_replay_store()),
            policy=MaintenanceControlPolicy(configuration=configuration, admission=admission,
                reservations=reservations),
            authorization_grants=SQLAlchemyWorkflowAuthorizationGrantService(engine))

    def retire_unstarted(reservation, *, lease):
        reservations.finish(reservation, status='cancelled', now=time.time(), lease=lease)

    runs = NativeMaintenanceRuns(runtime_factory=runtime, checkpoints=checkpoints,
        keys=get_workflow_authorization_key_ring, retire_unstarted=retire_unstarted)
    return NativeResultMaintenanceCadence(store=reservations, runs=runs,
        admission=admission, configuration=configuration)


def build_native_maintenance_admission(app, *, engine):
    selection = RolloutAwareRuntimeSelection(policies=production_rollout_policies(),
        selection=build_configured_workflow_runtime_selection(
            _NativeBackendDescriptor(),
            health=production_runtime_health(None), release_evidence=_CurrentReleaseAdmission(),
            native_production=True, registered_runtime_ids=('ananta-native',),
            capability_catalog=default_workflow_runtime_capability_service()))
    return RegisteredMaintenanceAdmission(engine=engine, registry=get_repository_registry,
        release=_CurrentReleaseAdmission(), selection=selection,
        governance=lambda: app.config.get('AGENT_CONFIG') or {})

def run_native_result_maintenance_tick(app):
    if str(app.config.get('ROLE') or '').lower() != 'hub':
        return {'status': 'disabled', 'reason_code': 'native_maintenance_hub_only'}
    with app.app_context():
        retirement_blocked = False
        try:
            from agent.services.native_result_retirement_runtime import get_native_result_retirement_service

            get_native_result_retirement_service(app).reconcile()
        except Exception:
            retirement_blocked = True
        try:
            service = app.extensions.get('native_result_maintenance_cadence')
            if service is None:
                service = build_native_result_maintenance_cadence(app)
                app.extensions['native_result_maintenance_cadence'] = service
            result = service.run_once()
            return {**result, 'retirement_reconciliation_blocked': True} if retirement_blocked else result
        except Exception:
            return {'status': 'blocked', 'reason_code': 'native_maintenance_composition_unavailable'}
