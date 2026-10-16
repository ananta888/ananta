"""Admin operator surface for bounded, fully headless development sessions."""
from flask import Blueprint, current_app, request

from agent.auth import admin_required
from agent.common.errors import api_response
from agent.services.self_development.contracts import form
from agent.services.self_development.runtime import get_development_coordinator

self_development_bp = Blueprint('self_development', __name__, url_prefix='/api/self-development')


def _service():
    service = get_development_coordinator()
    if service is None:
        raise ValueError('self_development_disabled')
    return service


def _respond(operation):
    try:
        return api_response(data=operation())
    except (ValueError, KeyError, TypeError) as exc:
        current_app.logger.warning('self_development_request_failed:%s', type(exc).__name__)
        return api_response(status='error', message=str(exc)[:240], code=422)


@self_development_bp.get('/form')
@admin_required
def task_form():
    return api_response(data=form())


@self_development_bp.get('/status')
@admin_required
def status():
    return _respond(lambda: _service().status())


@self_development_bp.post('/import')
@admin_required
def import_tasks():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or set(payload) - {'document'}:
        return api_response(status='error', message='invalid_import_request', code=422)
    return _respond(lambda: _service().import_tasks(payload.get('document')))


@self_development_bp.post('/start')
@admin_required
def start():
    def execute():
        service = _service()
        from agent.routes.tasks.autopilot import autonomous_loop
        team = service.policy.value['team_id']
        if autonomous_loop.running and autonomous_loop.team_id != team:
            raise ValueError('another_autopilot_team_is_running')
        result = service.start()
        autonomous_loop.start(team_id=team, goal='', max_concurrency=1, interval_seconds=5,
            security_level='balanced', budget_label='hub-verified-self-development')
        return result
    return _respond(execute)


@self_development_bp.post('/stop')
@admin_required
def stop():
    return _respond(lambda: _service().stop())


@self_development_bp.post('/retry')
@admin_required
def retry():
    return _respond(lambda: _service().retry())


@self_development_bp.get('/diagnostics')
@admin_required
def diagnostics():
    def read():
        _service()
        from agent.services.self_development.runtime import development_thread_diagnostics
        return development_thread_diagnostics()
    return _respond(read)
