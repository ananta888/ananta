"""Per-Hub Pi dispatch composition; no global service or deployment guessing."""

from typing import Protocol, runtime_checkable

PI_DISPATCH_SERVICE_KEY = 'ananta.pi_dispatch_evidence_service'


@runtime_checkable
class PiDispatchServicePort(Protocol):
    def prepare(self, *, task_id, assignment): ...
    def complete(self, *, task_id, assignment): ...
    def reserved_assignment(self, *, task_id): ...
    def complete_reserved(self, *, task_id): ...


def get_pi_dispatch_service(app):
    service = app.extensions.get(PI_DISPATCH_SERVICE_KEY)
    if service is None:
        return None
    if str(app.config.get('ROLE') or '').strip().lower() != 'hub':
        raise ValueError('pi_dispatch_composition_hub_required')
    if not isinstance(service, PiDispatchServicePort) or any(
            not callable(getattr(service, name, None))
            for name in ('prepare', 'complete', 'reserved_assignment', 'complete_reserved')):
        raise ValueError('pi_dispatch_composition_invalid')
    return service


def install_pi_dispatch_service(app, *, service):
    """Only trusted Hub bootstrap code installs independent deployment ports."""
    if str(app.config.get('ROLE') or '').strip().lower() != 'hub':
        raise ValueError('pi_dispatch_composition_hub_required')
    if not isinstance(service, PiDispatchServicePort) or any(
            not callable(getattr(service, name, None))
            for name in ('prepare', 'complete', 'reserved_assignment', 'complete_reserved')):
        raise ValueError('pi_dispatch_composition_invalid')
    if PI_DISPATCH_SERVICE_KEY in app.extensions:
        raise ValueError('pi_dispatch_composition_already_installed')
    app.extensions[PI_DISPATCH_SERVICE_KEY] = service


def read_pi_worker_evidence(**values):
    from flask import current_app

    service = get_pi_dispatch_service(current_app)
    if service is None:
        raise ValueError('pi_evidence_service_unavailable')
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise ValueError('pi_evidence_worker_read_binding_denied')
    return service.reserved_assignment(**values)


def read_pi_reserved_context(**values):
    from flask import current_app

    service = get_pi_dispatch_service(current_app)
    if service is None or not callable(getattr(service, 'reserved_context', None)):
        raise ValueError('pi_context_evidence_service_unavailable')
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise ValueError('pi_evidence_worker_read_binding_denied')
    return service.reserved_context(**values).to_dict()
