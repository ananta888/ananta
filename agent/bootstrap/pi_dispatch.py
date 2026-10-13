"""Explicit Hub startup composition; never infer deployment facts from Workers."""

from agent.services.pi_dispatch_runtime import install_pi_dispatch_service

DEPLOYMENTS_KEY = 'ananta.pi_runtime_deployments'
POLICY_KEY = 'ananta.pi_evidence_policy'
STATUS_KEY = 'ananta.pi_dispatch_startup_status'


def initialize_pi_dispatch(app, *, service_factory=None, observer_factory=None):
    config = app.config.get('AGENT_CONFIG') or {}
    if not isinstance(config, dict):
        raise ValueError('pi_dispatch_inventory_configuration_invalid')
    raw = config.get('pi_evidence', {})
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'bindings'}:
        raise ValueError('pi_dispatch_inventory_configuration_invalid')
    enabled = app.config.get('ANANTA_PI_EVIDENCE_ENABLED', raw.get('enabled', False))
    if type(enabled) is not bool:
        raise ValueError('pi_evidence_enabled_boolean_required')
    state = {'schema': 'ananta.pi-dispatch-startup.v1', 'state': 'disabled', 'reason_code': 'pi_evidence_disabled'}
    if enabled:
        if str(app.config.get('ROLE') or '').strip().lower() != 'hub':
            raise ValueError('pi_dispatch_composition_hub_required')
        deployments = app.extensions.get(DEPLOYMENTS_KEY)
        policy = app.extensions.get(POLICY_KEY)
        if deployments is None and policy is None and raw.get('bindings'):
            from agent.bootstrap.pi_dispatch_configuration import (
                compose_pi_dispatch_ports,
                production_pi_runtime_observer,
            )

            observer = (observer_factory or production_pi_runtime_observer)()
            deployments, policy = compose_pi_dispatch_ports(raw['bindings'], observer=observer)
        missing = []
        if not callable(getattr(deployments, 'require_manifest', None)):
            missing.append('deployment')
        if not callable(getattr(policy, 'digest_for_task', None)):
            missing.append('policy')
        if missing:
            state.update(state='blocked', reason_code='pi_evidence_independent_ports_unavailable',
                         missing_ports=missing)
        else:
            factory = service_factory or _production_service
            ports = {'deployments': deployments, 'policy_digest': policy.digest_for_task}
            destinations = app.extensions.get('source_control_destination_catalog')
            if destinations is not None:
                from agent.services.pi_approved_context_reader import TransactionPiApprovedContextReader

                ports['contexts'] = TransactionPiApprovedContextReader(destinations=destinations)
            service = factory(**ports)
            install_pi_dispatch_service(app, service=service)
            app.extensions[DEPLOYMENTS_KEY], app.extensions[POLICY_KEY] = deployments, policy
            state.update(state='ready', reason_code='pi_evidence_hub_composed')
    app.extensions[STATUS_KEY] = state
    return dict(state)


def _production_service(*, deployments, policy_digest, contexts=None):
    from agent.database import engine
    from agent.services.pi_dispatch_preparation import PiDispatchPreparationService
    from agent.services.pi_result_authority import SQLAlchemyPiResultAuthority
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring

    return PiDispatchPreparationService(engine=engine, deployments=deployments,
        authority=SQLAlchemyPiResultAuthority(key_ring=get_workflow_authorization_key_ring),
        policy_digest=policy_digest, signing_keys=get_workflow_authorization_key_ring, contexts=contexts)
